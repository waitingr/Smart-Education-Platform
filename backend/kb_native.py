"""kbcore 的 ctypes 封装：把知识库的逐块打分交给 C++ 内核。

为什么用 ctypes 而不是 pybind11：本机 Python 是 MSVC 编译的，MinGW 编出来的
扩展模块加载不了（ABI 不兼容）；而 **C ABI 的 DLL 通过 ctypes 调用与编译器无关**，
不需要装 pybind11，也不需要 Visual Studio 工具链。编坏的 DLL 删掉即可回退。

设计分工（与 native/kbcore.cpp 严格对应）：
    C++  ：BM25 打分、标题命中、正文覆盖率、成对概念惩罚、学科实体奖惩
    Python：分词（必须与旧实现逐字一致）、向量余弦（numpy/BLAS）、短语加分、
            引用格式化、排序与截断

C++ 只接收「已经分好的 token id 数组」，因此两边看到的分词结果完全一致，
可以用 scripts/verify_kb_equiv.py 直接做 top-10 逐位比对。
"""
import ctypes
import os
import threading

import numpy as np

_NATIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'native')

_LIB = None
_LIB_ERROR = ''
_LIB_LOCK = threading.Lock()

_CANDIDATE_NAMES = ('kbcore.dll', 'libkbcore.so', 'libkbcore.dylib')


def _load_library():
    """加载内核；找不到或加载失败返回 None，调用方据此回退纯 Python。"""
    global _LIB, _LIB_ERROR
    if _LIB is not None:
        return _LIB
    with _LIB_LOCK:
        if _LIB is not None:
            return _LIB
        path = os.getenv('KBCORE_DLL', '').strip()
        candidates = [path] if path else []
        candidates += [os.path.join(_NATIVE_DIR, name) for name in _CANDIDATE_NAMES]
        for candidate in candidates:
            if not candidate or not os.path.isfile(candidate):
                continue
            try:
                library = ctypes.CDLL(candidate)
                library.kbc_version.restype = ctypes.c_char_p
                library.kbc_index_create.restype = ctypes.c_void_p
                library.kbc_index_free.argtypes = [ctypes.c_void_p]
                _LIB = library
                _LIB_ERROR = ''
                return _LIB
            except OSError as error:
                _LIB_ERROR = f'{candidate}: {error}'
        if not _LIB_ERROR:
            _LIB_ERROR = f'未找到内核动态库（{", ".join(_CANDIDATE_NAMES)}），已回退纯 Python 引擎'
        return None


def available():
    return _load_library() is not None


def version():
    library = _load_library()
    if library is None:
        return ''
    return (library.kbc_version() or b'').decode('utf-8', 'replace')


def last_error():
    return _LIB_ERROR


def _ptr(array):
    return array.ctypes.data_as(ctypes.c_void_p)


class CppIndex:
    """把 _ChunkIndex 转成 CSR 结构并交给 C++。只在索引构建时做一次。"""

    def __init__(self, token_lists, title_sets, contrast_bits, entity_masks, authority, k1=1.5, b=0.75):
        self.library = _load_library()
        if self.library is None:
            raise RuntimeError(last_error() or 'kbcore 不可用')
        self.n_docs = len(token_lists)
        if not self.n_docs:
            raise RuntimeError('知识库为空')

        # 词表：正文 + 标题里出现的所有 token（标题词可能不在正文里）
        vocabulary = {}
        doc_ids = []
        for tokens in token_lists:
            ids = np.fromiter((vocabulary.setdefault(token, len(vocabulary)) for token in tokens),
                              dtype=np.int32, count=len(tokens))
            doc_ids.append(ids)
        title_ids_list = []
        for tokens in title_sets:
            ordered = sorted(tokens)
            title_ids_list.append(np.fromiter((vocabulary.setdefault(token, len(vocabulary)) for token in ordered),
                                              dtype=np.int32, count=len(ordered)))
        self.vocabulary = vocabulary
        self.n_vocab = len(vocabulary)

        self.doc_lens = np.fromiter((len(item) for item in doc_ids), dtype=np.int32, count=self.n_docs)

        # postings：按 (term, doc) 聚合，tf = 该词在同一块中出现的次数
        # 用复合键 term*n_docs+doc 排序 + 计数，一次 numpy 搞定，避免 12M 次 Python 循环
        if self.n_vocab and doc_ids:
            term_ids = np.concatenate(doc_ids).astype(np.int64)
            doc_ids_flat = np.repeat(np.arange(self.n_docs, dtype=np.int64), self.doc_lens.astype(np.int64))
            composite = term_ids * np.int64(self.n_docs) + doc_ids_flat
            unique, counts = np.unique(composite, return_counts=True)
            term_of = (unique // np.int64(self.n_docs)).astype(np.int32)
            doc_of = (unique % np.int64(self.n_docs)).astype(np.int32)
            self.post_doc = doc_of
            self.post_tf = counts.astype(np.int32)
            per_term = np.bincount(term_of, minlength=self.n_vocab).astype(np.int64)
            self.post_ptr = np.zeros(self.n_vocab + 1, dtype=np.int64)
            np.cumsum(per_term, out=self.post_ptr[1:])
        else:  # pragma: no cover - 空词表不会走到
            self.post_doc = np.zeros(0, dtype=np.int32)
            self.post_tf = np.zeros(0, dtype=np.int32)
            self.post_ptr = np.zeros(self.n_vocab + 1, dtype=np.int64)

        # 标题 CSR
        title_lengths = np.asarray([len(item) for item in title_ids_list], dtype=np.int64)
        self.title_ptr = np.zeros(self.n_docs + 1, dtype=np.int64)
        np.cumsum(title_lengths, out=self.title_ptr[1:])
        self.title_ids = (np.concatenate(title_ids_list) if title_ids_list
                          else np.zeros(0, dtype=np.int32)).astype(np.int32)

        self.contrast_bits = np.asarray(contrast_bits, dtype=np.int32)
        self.entity_masks = np.asarray(entity_masks, dtype=np.int64)
        self.authority = np.asarray(authority, dtype=np.int32)

        self.handle = ctypes.c_void_p(self.library.kbc_index_create(
            ctypes.c_int32(self.n_docs),
            ctypes.c_int32(self.n_vocab),
            _ptr(self.doc_lens),
            _ptr(self.title_ptr),
            _ptr(self.title_ids),
            ctypes.c_int64(self.title_ids.size),
            _ptr(self.post_ptr),
            _ptr(self.post_doc),
            _ptr(self.post_tf),
            ctypes.c_int64(self.post_doc.size),
            _ptr(self.contrast_bits),
            _ptr(self.entity_masks),
            _ptr(self.authority),
            ctypes.c_float(k1),
            ctypes.c_float(b),
        ))
        if not self.handle or not self.handle.value:
            raise RuntimeError('kbcore 索引创建失败')

    def close(self):
        if getattr(self, 'handle', None):
            if self.handle.value:
                self.library.kbc_index_free(self.handle)
            self.handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def version(self):
        return (self.library.kbc_version() or b'').decode('utf-8', 'replace')

    def ids_for(self, tokens):
        """把查询 token 映射成词表 id；词表里没有的词给 -1（C++ 侧忽略）。"""
        getter = self.vocabulary.get
        return np.fromiter((getter(token, -1) for token in tokens), dtype=np.int32, count=len(tokens))

    def score(self, query_ids, query_total, ask_bits, entity_mask, prefer_flags,
              authority_weight=0.1, title_weight=0.18, coverage_weight=0.55,
              prefer_bonus=0.08, contrast_weight=0.20, entity_bonus=0.20, entity_penalty=0.20):
        """返回 (lexical, delta, auth)：原实现里的 BM25 原始分、附加分、权威度因子。"""
        n_docs = self.n_docs
        lexical = np.zeros(n_docs, dtype=np.float32)
        delta = np.zeros(n_docs, dtype=np.float32)
        auth = np.zeros(n_docs, dtype=np.float32)
        flags = np.ascontiguousarray(prefer_flags, dtype=np.int8) if prefer_flags is not None else None
        self.library.kbc_search.restype = ctypes.c_int32
        self.library.kbc_search(
            self.handle,
            _ptr(np.ascontiguousarray(query_ids, dtype=np.int32)),
            ctypes.c_int32(int(query_ids.size)),
            ctypes.c_int32(int(query_total)),
            ctypes.c_int32(int(ask_bits)),
            ctypes.c_int64(int(entity_mask)),
            _ptr(flags) if flags is not None else None,
            ctypes.c_float(authority_weight),
            ctypes.c_float(title_weight),
            ctypes.c_float(coverage_weight),
            ctypes.c_float(prefer_bonus),
            ctypes.c_float(contrast_weight),
            ctypes.c_float(entity_bonus),
            ctypes.c_float(entity_penalty),
            _ptr(lexical),
            _ptr(delta),
            _ptr(auth),
        )
        return lexical, delta, auth