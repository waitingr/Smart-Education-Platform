"""学科知识库服务：结构化元数据 + 语义分块 + 混合检索 + 可追溯引用。

与旧版差异（对应赛事「知识权威可信」「内容可追溯」「知识库构建」要求）：
- 每个知识块都携带来源元数据（教材/论文/标准/案例、作者、年份、出版社、权威等级）；
- 分块以 Markdown 章节为锚点，引用可定位到「文档 · 章节 · 页码」；
- BM25（字二元组）+ 向量余弦混合检索，并叠加标题/章节命中与权威度权重；
- 支持在线增量入库（教师上传文档即时生效），索引可一键重建；
- 无 GPU / 无 sentence-transformers 时自动使用纯 numpy 哈希向量，保证可离线复现。
"""
import hashlib
import json
import os
import re
import threading
import time

import numpy as np

from config import Config
from doc_parser import SUPPORTED_ALL, parse_file
from text_utils import (BM25, CONTRAST_PAIRS, DISCIPLINE_ENTITIES, clip,
                        content_tokens, hashing_embed, normalize,
                        overlap_score, tokenize)

INDEX_DIR = Config.KB_CACHE_DIR
CHUNKS_FILE = os.path.join(INDEX_DIR, 'kb_chunks.json')
VECTORS_FILE = os.path.join(INDEX_DIR, 'kb_vectors.npy')
META_FILE = os.path.join(INDEX_DIR, 'kb_index.json')
HASH_DIM = 512
PAGE_RE = re.compile(r'【第(\d+)页】')
HEADING_RE = re.compile(r'^(#{1,4})\s+(.*)$')

SOURCE_TYPES = {
    'textbook': '教材讲义',
    'paper': '学术论文',
    'standard': '行业标准/规范',
    'case': '教学案例/题库',
    'syllabus': '课程标准/大纲',
    'upload': '教师上传资料',
}


# ==================== 元数据（极简 YAML 前置块） ====================
def parse_front_matter(text):
    """解析 Markdown 文首的 --- 元数据块，返回 (meta, body)。"""
    text = normalize(text)
    if not text.startswith('---'):
        return {}, text
    lines = text.split('\n')
    meta, end = {}, None
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() in ('---', '...'):
            end = index
            break
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not key:
            continue
        if value.startswith('[') and value.endswith(']'):
            meta[key] = [item.strip().strip('"').strip("'") for item in value[1:-1].split(',') if item.strip()]
        else:
            meta[key] = value
    if end is None:
        return {}, text
    return meta, '\n'.join(lines[end + 1:]).lstrip('\n')


def _as_int(value, default=3):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# ==================== 嵌入后端 ====================
def _find_embedding_model_dir():
    if Config.EMBEDDING_MODEL_PATH and os.path.isdir(Config.EMBEDDING_MODEL_PATH):
        return Config.EMBEDDING_MODEL_PATH
    models_root = os.path.join(Config.KB_CACHE_DIR, '..', '..', 'models')
    models_root = os.path.abspath(models_root)
    if not os.path.isdir(models_root):
        return None
    skip = {'llm'}
    for current, dirs, files in os.walk(models_root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith('.')]
        if 'config.json' in files and any(
            name in files for name in ('model.safetensors', 'pytorch_model.bin', 'model.onnx')
        ):
            return current
    return None


class Embedder:
    """三级降级嵌入：sentence-transformers → transformers 均值池化 → 哈希向量。"""

    def __init__(self):
        self.backend = 'hash'
        self.dim = HASH_DIM
        self.model = None
        self._resolved = False

    def resolve(self):
        if self._resolved:
            return self.backend
        self._resolved = True
        requested = Config.EMBEDDING_BACKEND
        if requested in ('auto', 'sentence-transformers'):
            model_dir = _find_embedding_model_dir()
            if model_dir:
                try:
                    from sentence_transformers import SentenceTransformer
                    self.model = SentenceTransformer(model_dir, device='cpu')
                    probe = self.model.encode(['测试'], convert_to_numpy=True)
                    self.dim = int(np.asarray(probe).shape[1])
                    self.backend = 'sentence-transformers'
                    return self.backend
                except Exception as error:
                    print(f'[KB] sentence-transformers 不可用（{error}），尝试 transformers 均值池化')
        if requested in ('auto', 'transformers'):
            model_dir = _find_embedding_model_dir()
            if model_dir:
                try:
                    import torch
                    from transformers import AutoModel, AutoTokenizer
                    self._tokenizer = AutoTokenizer.from_pretrained(model_dir)
                    self.model = AutoModel.from_pretrained(model_dir)
                    self.model.eval()
                    probe = self._encode_transformers(['测试'])
                    self.dim = int(probe.shape[1])
                    self.backend = 'transformers'
                    return self.backend
                except Exception as error:
                    print(f'[KB] transformers 嵌入不可用（{error}），降级为哈希向量')
        self.backend = 'hash'
        self.dim = HASH_DIM
        return self.backend

    def _encode_transformers(self, texts):
        import torch
        batch = self._tokenizer(texts, return_tensors='pt', truncation=True,
                                max_length=512, padding=True)
        with torch.no_grad():
            output = self.model(**batch)
        token_embeddings = output.last_hidden_state
        mask = batch['attention_mask'].unsqueeze(-1).expand(token_embeddings.size()).float()
        summed = (token_embeddings * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-6)
        vectors = (summed / counts).cpu().numpy().astype('float32')
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms

    def encode(self, texts):
        self.resolve()
        if self.backend == 'sentence-transformers':
            vectors = self.model.encode(list(texts), convert_to_numpy=True,
                                        batch_size=16, show_progress_bar=False)
            vectors = np.asarray(vectors, dtype='float32')
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return vectors / norms
        if self.backend == 'transformers':
            batches = []
            for start in range(0, len(texts), 16):
                batches.append(self._encode_transformers(list(texts[start:start + 16])))
            return np.vstack(batches) if batches else np.zeros((0, self.dim), dtype='float32')
        vectors = np.vstack([hashing_embed(text, self.dim) for text in texts]) if texts else np.zeros((0, self.dim), dtype='float32')
        return vectors.astype('float32')


embedder = Embedder()


# ==================== 分块 ====================
def split_sections(text, doc_title=None):
    """按 Markdown 标题切分为 (章节路径, 正文) 列表，忽略代码块内的 # 号。"""
    sections = []
    stack = []
    current = {'path': '', 'lines': []}
    in_fence = False
    for line in normalize(text).split('\n'):
        if line.strip().startswith('```'):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if match:
            if current['lines']:
                sections.append((current['path'], '\n'.join(current['lines']).strip()))
            level = len(match.group(1))
            title = match.group(2).strip()
            stack = stack[:level - 1] + [title]
            current = {'path': ' > '.join(stack), 'lines': []}
        else:
            current['lines'].append(line)
    if current['lines']:
        sections.append((current['path'], '\n'.join(current['lines']).strip()))
    if not sections:
        body = normalize(text).strip()
        if body:
            sections = [('', body)]
    # 去掉与文档标题重复的一级标题，避免「标题 · 标题 > 小节」的冗余定位
    if doc_title:
        cleaned_title = doc_title.strip()
        sections = [
            (' > '.join(part for part in path.split(' > ') if part.strip() != cleaned_title), body)
            for path, body in sections
        ]
    return [(path, body) for path, body in sections if body.strip()]


def chunk_body(path, body, max_chars):
    if len(body) <= max_chars:
        return [(path, body)]
    paragraphs = [p for p in re.split(r'\n\s*\n', body) if p.strip()]
    chunks, buffer, overlap_tail = [], '', ''
    for paragraph in paragraphs:
        candidate = (buffer + '\n\n' + paragraph).strip() if buffer else paragraph
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            chunks.append((path, buffer))
            overlap_tail = buffer[-120:]
            buffer = (overlap_tail + '\n\n' + paragraph).strip()
        else:
            # 单段超长，硬切
            for start in range(0, len(paragraph), max_chars):
                pieces = paragraph[start:start + max_chars]
                chunks.append((path, pieces))
            buffer = ''
    if buffer.strip():
        chunks.append((path, buffer))
    return chunks


def _doc_meta(path):
    extension = os.path.splitext(path)[1].lower()
    meta = {}
    if extension in ('.md', '.markdown', '.txt'):
        try:
            with open(path, 'r', encoding='utf-8-sig', errors='replace') as handle:
                raw = handle.read(4000)
            front, _ = parse_front_matter(raw)
            meta.update(front)
        except Exception:
            pass
    sidecar = path + '.meta.json'
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, 'r', encoding='utf-8') as handle:
                meta.update(json.load(handle))
        except Exception:
            pass
    return meta


def _make_doc_id(path):
    relative = os.path.relpath(path, Config.KB_DOC_DIR).replace('\\', '/')
    return hashlib.md5(relative.encode('utf-8')).hexdigest()[:12], relative


def _engine_choice():
    """检索引擎：auto（默认，有 C++ 内核就用）/ python / cpp。"""
    choice = (os.getenv('KB_ENGINE') or getattr(Config, 'KB_ENGINE', 'auto') or 'auto').strip().lower()
    if choice not in ('auto', 'python', 'cpp'):
        choice = 'auto'
    if choice == 'python':
        return 'python'
    try:
        import kb_native
        if kb_native.available():
            return 'cpp'
        if choice == 'cpp':
            print(f'[KB] KB_ENGINE=cpp 但内核不可用：{kb_native.last_error()}')
    except Exception as error:  # noqa: BLE001 - 缺 DLL 时静默回退
        if choice == 'cpp':
            print(f'[KB] KB_ENGINE=cpp 但加载内核失败：{error}')
    return 'python'


class _ChunkIndex:
    """知识块的预计算索引：分词与集合特征只在加载/重建时算一次。

    为什么要有它：原实现每次 search 都把每个块的正文重新分词 4~5 次
    （BM25 用一次、标题一次、覆盖率两次、短语一次），81 块就要 15~36 ms，
    4.8 万块会到 9 秒以上。检索的热路径应该只做「查询侧」计算 + 集合运算。

    这里同时把 C++ 内核需要的数据准备好（`CppIndex` 会复用 token 列表），
    保证 Python 引擎与 C++ 引擎拿到的是同一份分词结果，两者可直接做等价对比。
    """

    def __init__(self, chunks):
        self.chunks = chunks
        self.tokens = [tokenize(chunk['text']) for chunk in chunks]
        self.token_sets = [set(tokens) for tokens in self.tokens]
        self.token_strs = [' '.join(tokens) for tokens in self.tokens]
        self.title_sets = [set(tokenize(chunk['doc_title'] + ' ' + chunk['section'])) for chunk in chunks]
        self.bm25 = BM25(self.tokens)
        # 成对概念：每块预判 has_a / has_b（第 i 对占 2i / 2i+1 位）
        self.contrast_bits = []
        for chunk in chunks:
            text = chunk['text']
            bits = 0
            for position, (pattern_a, pattern_b) in enumerate(CONTRAST_PAIRS):
                if re.search(pattern_a, text):
                    bits |= 1 << (2 * position)
                if re.search(pattern_b, text):
                    bits |= 1 << (2 * position + 1)
            self.contrast_bits.append(bits)
        # 学科实体：每块命中哪些实体（下标即位号）
        self.entity_masks = []
        for chunk in chunks:
            text = chunk['text']
            mask = 0
            for position, entity in enumerate(DISCIPLINE_ENTITIES):
                if entity in text:
                    mask |= 1 << position
            self.entity_masks.append(mask)

        # 权威度因子按查询参数算（authority 只有 1~5 五档，numpy 一次算完）
        self.authority = np.fromiter(
            (max(1, min(int(chunk.get('authority') or 3), 5)) for chunk in chunks),
            dtype=np.int32, count=len(chunks),
        )
        self._auth_cache = {}

        # 引擎选择：默认 auto（有 C++ 内核就用），可被 KB_ENGINE=python 强制回退
        self.cpp = None
        self.engine = 'python'
        self.engine_error = ''
        if _engine_choice() == 'cpp':
            try:
                from kb_native import CppIndex
                self.cpp = CppIndex(
                    self.tokens,
                    self.title_sets,
                    self.contrast_bits,
                    self.entity_masks,
                    self.authority,
                )
                self.engine = 'cpp'
            except Exception as error:  # noqa: BLE001 - 任何问题都回退，绝不让检索失败
                self.engine_error = f'{type(error).__name__}: {error}'
                self.cpp = None
                print(f'[KB] C++ 内核不可用（{self.engine_error}），已回退纯 Python 引擎')
        else:
            # 记录「为什么没用 C++」，健康检查里能看到，避免以为在跑内核其实没有
            try:
                import kb_native
                self.engine_error = kb_native.last_error()
            except Exception:
                self.engine_error = '未安装 kb_native 模块'

    def authority_factors(self, authority_weight):
        """(1 - w*(a-1)) 五档因子，按 w 缓存，避免每次查询重算。"""
        cached = self._auth_cache.get(authority_weight)
        if cached is None:
            cached = (1.0 - np.float32(authority_weight) * (self.authority.astype('float32') - np.float32(1.0)))
            self._auth_cache[authority_weight] = cached
        return cached

    def prefer_flags(self, prefer_types):
        """把 prefer_types 集合展开成逐块 0/1 标记，交给 C++ 内核。"""
        if not prefer_types:
            return np.zeros(len(self.chunks), dtype=np.int8)
        return np.fromiter(
            (1 if chunk['source_type'] in prefer_types else 0 for chunk in self.chunks),
            dtype=np.int8, count=len(self.chunks),
        )


def _contrast_query_bits(question):
    """查询侧：问的是成对概念里的哪一方（与 _ChunkIndex 的位布局一致）。"""
    bits = 0
    for position, (pattern_a, pattern_b) in enumerate(CONTRAST_PAIRS):
        if re.search(pattern_a, question):
            bits |= 1 << (2 * position)
        if re.search(pattern_b, question):
            bits |= 1 << (2 * position + 1)
    return bits


def _contrast_penalty(ask_bits, doc_bits, weight):
    """等价于 text_utils.contrast_penalty，但两侧都已是位掩码。"""
    penalty = 0.0
    for position in range(len(CONTRAST_PAIRS)):
        ask_a = (ask_bits >> (2 * position)) & 1
        ask_b = (ask_bits >> (2 * position + 1)) & 1
        if ask_a == ask_b:
            continue
        has_a = (doc_bits >> (2 * position)) & 1
        has_b = (doc_bits >> (2 * position + 1)) & 1
        if ask_a and has_b and not has_a:
            penalty += weight
        elif ask_b and has_a and not has_b:
            penalty += weight
    return min(penalty, 0.5)


def _entity_query_mask(question):
    mask = 0
    for position, entity in enumerate(DISCIPLINE_ENTITIES):
        if entity in question:
            mask |= 1 << position
    return mask


def _entity_score(asked_mask, doc_mask, bonus, penalty):
    """等价于 text_utils.entity_affinity，但两侧都已是位掩码。"""
    if not asked_mask or not doc_mask:
        return 0.0
    if asked_mask & doc_mask:
        return bonus
    return -penalty if doc_mask else 0.0


class KnowledgeBase:
    def __init__(self):
        self.lock = threading.Lock()
        self.chunks = []
        self.vectors = None
        self.meta = {}
        self._index = None
        self._loaded_mtime = None

    # ---------------- 索引构建 ----------------
    def build(self, verbose=True):
        started = time.time()
        os.makedirs(INDEX_DIR, exist_ok=True)
        os.makedirs(Config.KB_DOC_DIR, exist_ok=True)
        chunks = []
        documents = []
        for current, dirs, files in os.walk(Config.KB_DOC_DIR):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for filename in sorted(files):
                if filename.startswith('.'):
                    continue
                path = os.path.join(current, filename)
                if os.path.splitext(filename)[1].lower() not in SUPPORTED_ALL:
                    continue
                try:
                    parsed = parse_file(path)
                except ValueError as error:
                    if verbose:
                        print(f'[KB] 跳过 {filename}：{error}')
                    continue
                body = parsed.text
                file_meta = _doc_meta(path)
                if parsed.kind == 'text' or parsed.kind == 'docx':
                    front, body = parse_front_matter(body)
                    file_meta = {**front, **file_meta}
                doc_id, relative = _make_doc_id(path)
                title = file_meta.get('title') or os.path.splitext(filename)[0]
                source_type = str(file_meta.get('source_type') or 'textbook').lower()
                if source_type not in SOURCE_TYPES:
                    source_type = 'textbook'
                authority = _as_int(file_meta.get('authority'), 2)
                record = {
                    'doc_id': doc_id,
                    'title': title,
                    'relative_path': relative,
                    'source_type': source_type,
                    'source_type_label': SOURCE_TYPES.get(source_type, '其他'),
                    'authors': file_meta.get('authors') or '',
                    'year': str(file_meta.get('year') or ''),
                    'publisher': file_meta.get('publisher') or '',
                    'authority': max(1, min(authority, 5)),
                    'tags': file_meta.get('tags') if isinstance(file_meta.get('tags'), list) else [],
                    'kind': parsed.kind,
                    'chars': len(body),
                }
                doc_chunks = 0
                for section_path, section_body in split_sections(body, title):
                    for chunk_path, text in chunk_body(section_path, section_body, Config.KB_MAX_CHUNK_CHARS):
                        text = text.strip()
                        if len(text) < 20:
                            continue
                        page_match = PAGE_RE.search(text)
                        chunks.append({
                            'doc_id': doc_id,
                            'doc_title': title,
                            'section': chunk_path,
                            'text': text,
                            'page': int(page_match.group(1)) if page_match else None,
                            'source_type': source_type,
                            'source_type_label': record['source_type_label'],
                            'authors': record['authors'],
                            'year': record['year'],
                            'publisher': record['publisher'],
                            'authority': record['authority'],
                        })
                        doc_chunks += 1
                record['chunks'] = doc_chunks
                documents.append(record)
                if verbose:
                    print(f'[KB] 已索引 {filename} → {doc_chunks} 块')

        embed_texts = [f"{chunk['doc_title']} {chunk['section']} {chunk['text']}" for chunk in chunks]
        vectors = embedder.encode(embed_texts) if embed_texts else np.zeros((0, embedder.dim), dtype='float32')
        for index, chunk in enumerate(chunks, start=1):
            chunk['index'] = index
            chunk['locator'] = f"{chunk['doc_title']}" + (f" · {chunk['section']}" if chunk['section'] else '')
            if chunk['page']:
                chunk['locator'] += f" · 第{chunk['page']}页"
            chunk['snippet'] = clip(chunk['text'].replace('\n', ' '), 160)

        meta = {
            'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'backend': embedder.backend,
            'dim': int(vectors.shape[1]) if vectors.size else embedder.dim,
            'chunk_count': len(chunks),
            'doc_count': len(documents),
            'documents': documents,
            'build_seconds': round(time.time() - started, 2),
        }
        with open(CHUNKS_FILE, 'w', encoding='utf-8') as handle:
            json.dump(chunks, handle, ensure_ascii=False)
        np.save(VECTORS_FILE, vectors.astype('float32'))
        with open(META_FILE, 'w', encoding='utf-8') as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2)

        self.chunks = chunks
        self.vectors = vectors.astype('float32')
        self.meta = meta
        self._index = None
        self._loaded_mtime = os.path.getmtime(META_FILE)
        if verbose:
            print(f"[KB] 索引完成：{len(documents)} 篇文档 / {len(chunks)} 个知识块 / 向量后端 {embedder.backend}")
        return meta

    # ---------------- 索引加载 ----------------
    def ensure(self, rebuild_if_missing=True):
        with self.lock:
            if self.chunks and self.vectors is not None:
                return self
            if not (os.path.isfile(CHUNKS_FILE) and os.path.isfile(VECTORS_FILE) and os.path.isfile(META_FILE)):
                if rebuild_if_missing:
                    self.build()
                return self
            try:
                with open(META_FILE, 'r', encoding='utf-8') as handle:
                    meta = json.load(handle)
                with open(CHUNKS_FILE, 'r', encoding='utf-8') as handle:
                    chunks = json.load(handle)
                vectors = np.load(VECTORS_FILE)
            except Exception as error:
                print(f'[KB] 索引损坏（{error}），正在重建')
                if rebuild_if_missing:
                    self.build()
                return self
            expected_dim = embedder.dim if embedder.backend != 'hash' else HASH_DIM
            embedder.resolve()
            if meta.get('backend') != embedder.backend or int(meta.get('dim') or 0) != int(vectors.shape[1]):
                print(f"[KB] 嵌入后端变化（{meta.get('backend')} → {embedder.backend}），自动重建索引")
                if rebuild_if_missing:
                    self.build()
                return self
            self.chunks, self.vectors, self.meta = chunks, vectors.astype('float32'), meta
            self._index = None
            self._loaded_mtime = os.path.getmtime(META_FILE)
            return self

    def _ensure_index(self):
        """构建（或复用）预计算索引；索引随知识块一起失效。"""
        if self._index is None:
            with self.lock:
                if self._index is None:
                    self._index = _ChunkIndex(self.chunks)
        return self._index

    # ---------------- 检索 ----------------
    def _score_python(self, index, search_tokens, query_tokens, ask_bits, asked_mask, prefer_types, authority_weight):
        """纯 Python 引擎：BM25 + 附加分，全部基于预计算集合。"""
        raw = np.asarray(index.bm25.scores(search_tokens), dtype='float32')
        delta = np.zeros(len(self.chunks), dtype='float32')
        query_size = len(query_tokens) or 1
        title_sets = index.title_sets
        token_sets = index.token_sets
        contrast_bits = index.contrast_bits
        entity_masks = index.entity_masks
        for position, chunk in enumerate(self.chunks):
            extra = 0.0
            if query_tokens:
                # 标题/章节命中 + 正文覆盖率：块内特征已预计算，这里只做集合交
                extra += 0.18 * (len(query_tokens & title_sets[position]) / query_size)
                extra += 0.55 * (len(query_tokens & token_sets[position]) / query_size)
            if chunk['source_type'] in prefer_types:
                extra += 0.08
            # 成对概念消歧：问「时间复杂度」时不要把「空间复杂度」段落排到前面
            extra -= _contrast_penalty(ask_bits, contrast_bits[position], 0.20)
            # 实体消歧：问快速排序时，只讲希尔排序的段落要沉下去
            extra += _entity_score(asked_mask, entity_masks[position], 0.20, 0.20)
            delta[position] = extra
        auth = index.authority_factors(authority_weight)
        return raw, delta, auth

    def _score_cpp(self, index, search_tokens, query_tokens, ask_bits, asked_mask, prefer_types, authority_weight):
        """C++ 内核：同样的公式，逐块打分的循环在 native/kbcore 里。"""
        prefer_flags = index.prefer_flags(prefer_types)
        query_ids = index.cpp.ids_for(search_tokens)
        raw, delta, auth = index.cpp.score(
            query_ids,
            len(query_tokens) or 1,
            ask_bits,
            asked_mask,
            prefer_flags,
            authority_weight,
        )
        return raw, delta, auth

    def search(self, query, top_k=None, prefer_types=None, authority_weight=0.1):
        self.ensure()
        top_k = top_k or Config.KB_TOP_K
        if not self.chunks:
            return []
        query = (query or '').strip()
        if not query:
            return []
        index = self._ensure_index()
        prefer_types = set(prefer_types or [])
        search_tokens = content_tokens(query)
        query_tokens = set(search_tokens)
        # 查询侧只算一次：成对概念问的是哪一方、点名了哪些学科实体
        ask_bits = _contrast_query_bits(query)
        asked_mask = _entity_query_mask(query)

        if index.cpp is not None:
            lexical, delta, auth = self._score_cpp(index, search_tokens, query_tokens, ask_bits,
                                                   asked_mask, prefer_types, authority_weight)
        else:
            lexical, delta, auth = self._score_python(index, search_tokens, query_tokens, ask_bits,
                                                      asked_mask, prefer_types, authority_weight)

        lexical = np.asarray(lexical, dtype='float32')
        if lexical.size and lexical.max() > 0:
            lexical = lexical / lexical.max()
        if self.vectors is not None and len(self.vectors) == len(self.chunks) and self.vectors.size:
            query_vector = embedder.encode([query])[0]
            semantic = self.vectors @ query_vector
            semantic = np.maximum(semantic, 0.0)
            if semantic.max() > 0:
                semantic = semantic / semantic.max()
            # 哈希向量只保留词面重合的粗粒度信息，噪声较大，降权以免压过 BM25 的精确匹配
            semantic_weight = 0.25 if (self.meta or {}).get('backend', 'hash') == 'hash' else 0.55
            combined = semantic_weight * semantic + (1.0 - semantic_weight) * lexical
        else:
            combined = lexical

        # 连续短语命中（长 n-gram）是强相关信号。短语加分最多 0.15，
        # 所以只需检查「有可能因此进入 top_k」的块，避免大库上做 N×P 次子串查找。
        query_text = ' '.join(search_tokens)
        query_phrases = tuple({query_text[i:i + 12] for i in range(max(0, len(query_text) - 11))})
        pre = combined.astype('float64') + delta
        base = pre * auth
        if query_phrases and pre.size:
            if pre.size > top_k:
                kth = np.partition(base, base.size - top_k)[base.size - top_k]
            else:
                kth = float(base.min())
            threshold = kth - 0.15 - 1e-6
            token_strs = index.token_strs
            for position in np.nonzero(base >= threshold)[0]:
                chunk_text = token_strs[position]
                for phrase in query_phrases:
                    if phrase in chunk_text:
                        pre[position] += 0.15
                        break
            base = pre * auth

        order = np.argsort(-base, kind='stable')[:top_k]

        token_sets = index.token_sets
        raw_query_tokens = set(tokenize(query))
        results = []
        for rank, position in enumerate(order.tolist(), start=1):
            chunk = self.chunks[position]
            score = float(base[position])
            if raw_query_tokens:
                coverage = len(raw_query_tokens & token_sets[position]) / len(raw_query_tokens)
            else:
                coverage = 0.0
            results.append({
                'index': rank,
                'chunk_id': chunk.get('index'),
                'doc_id': chunk['doc_id'],
                'doc_title': chunk['doc_title'],
                'section': chunk['section'],
                'locator': chunk['locator'],
                'page': chunk.get('page'),
                'text': chunk['text'],
                'snippet': chunk['snippet'],
                'score': round(min(score, 1.5), 4),
                'coverage': round(coverage, 3),
                'source_type': chunk['source_type'],
                'source_type_label': chunk['source_type_label'],
                'authors': chunk['authors'],
                'year': chunk['year'],
                'publisher': chunk['publisher'],
                'authority': chunk['authority'],
                'citation': self.format_citation(chunk),
            })
        return results

    @staticmethod
    def format_citation(chunk):
        parts = [chunk['doc_title']]
        if chunk.get('authors'):
            parts.append(str(chunk['authors']))
        if chunk.get('publisher'):
            parts.append(str(chunk['publisher']))
        if chunk.get('year'):
            parts.append(f"{chunk['year']} 年")
        if chunk.get('section'):
            parts.append(chunk['section'])
        if chunk.get('page'):
            parts.append(f"第 {chunk['page']} 页")
        return f"{parts[0]}（{'，'.join(parts[1:])}）" if len(parts) > 1 else parts[0]

    def context_block(self, results, max_chars=3600):
        blocks, used = [], 0
        for item in results:
            text = item['text']
            if used + len(text) > max_chars:
                text = clip(text, max(0, max_chars - used))
            if not text:
                break
            blocks.append(f"[{item['index']}] 来源：{item['citation']}\n{text}")
            used += len(text)
            if used >= max_chars:
                break
        return '\n\n'.join(blocks)

    # ---------------- 文档管理 ----------------
    def stats(self):
        self.ensure()
        by_type = {}
        for document in self.meta.get('documents', []):
            label = document.get('source_type_label', '其他')
            by_type[label] = by_type.get(label, 0) + 1
        lengths = [len(chunk.get('text') or '') for chunk in self.chunks]
        try:
            index = self._ensure_index()
            engine = index.engine
            engine_version = index.cpp.version() if index.cpp is not None else 'pure python'
        except Exception as error:  # 索引构建失败时健康检查仍要能返回
            engine, engine_version = 'unknown', f'{type(error).__name__}: {error}'
        return {
            'doc_count': self.meta.get('doc_count', 0),
            'chunk_count': self.meta.get('chunk_count', 0),
            'built_at': self.meta.get('built_at'),
            'backend': self.meta.get('backend'),
            'dim': self.meta.get('dim'),
            'by_type': by_type,
            'avg_chunk_chars': round(sum(lengths) / len(lengths)) if lengths else 0,
            'top_k': Config.KB_TOP_K,
            'engine': engine,
            'engine_version': engine_version,
            'engine_note': getattr(index, 'engine_error', '') or '',
        }

    def list_documents(self):
        self.ensure()
        return sorted(self.meta.get('documents', []), key=lambda item: (item.get('source_type') or '', item.get('title') or ''))

    def add_document(self, file_path, title=None, source_type='upload', meta=None):
        """把文件纳入知识库并重建索引。返回新增文档元数据。"""
        os.makedirs(os.path.join(Config.KB_DOC_DIR, '教师上传'), exist_ok=True)
        target_name = os.path.basename(file_path)
        target = os.path.join(Config.KB_DOC_DIR, '教师上传', target_name)
        stem, extension = os.path.splitext(target)
        counter = 1
        while os.path.exists(target):
            target = f'{stem}_{counter}{extension}'
            counter += 1
        with open(file_path, 'rb') as source, open(target, 'wb') as destination:
            destination.write(source.read())
        document_meta = {'title': title or os.path.splitext(target_name)[0], 'source_type': source_type}
        if meta:
            document_meta.update(meta)
        with open(target + '.meta.json', 'w', encoding='utf-8') as handle:
            json.dump(document_meta, handle, ensure_ascii=False, indent=2)
        self.build(verbose=False)
        for document in self.meta.get('documents', []):
            if document['relative_path'].endswith(os.path.basename(target)):
                return document
        return {'title': document_meta['title']}

    def delete_document(self, doc_id):
        self.ensure()
        target = None
        for document in self.meta.get('documents', []):
            if document.get('doc_id') == doc_id:
                target = os.path.join(Config.KB_DOC_DIR, document['relative_path'])
                break
        if not target or not os.path.isfile(target):
            return False
        try:
            os.remove(target)
            sidecar = target + '.meta.json'
            if os.path.isfile(sidecar):
                os.remove(sidecar)
        except OSError as error:
            print(f'[KB] 删除失败：{error}')
            return False
        self.build(verbose=False)
        return True


_kb = None


def get_kb():
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
        _kb.ensure()
    return _kb


def rebuild_kb():
    kb = get_kb()
    return kb.build()


def reset_kb():
    global _kb
    _kb = None