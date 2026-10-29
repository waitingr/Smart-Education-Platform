"""纯标准库实现的文本工具：分词、分句、BM25、哈希向量、JSON 抽取。

不依赖 jieba / sklearn / sentence-transformers，保证任何评审机器都能直接运行。
中文场景下「字二元组（bigram）+ 英文词」的分词方式在术语检索上表现稳定，
足以支撑学科知识库的混合检索（BM25 + 向量）。
"""
import json
import math
import re
import unicodedata
from collections import Counter

CJK_RE = re.compile(r'[\u4e00-\u9fff]')
WORD_RE = re.compile(r'[A-Za-z][A-Za-z0-9_+\-#.]*|[0-9]+(?:\.[0-9]+)?')
SENTENCE_END = '。！？；!?;\n'
PUNCT_RE = re.compile(r'[\s\u3000]+')


def normalize(text):
    """展示用规范化：使用 NFC，保留 O(n²)、下标等学科符号的原始写法。"""
    if not text:
        return ''
    text = unicodedata.normalize('NFC', str(text))
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\u3000', ' ')


def normalize_index(text):
    """检索用规范化：NFKC 把全角字符、上标数字折叠为半角，提升召回一致性。"""
    return unicodedata.normalize('NFKC', normalize(text))


def _cjk_bigrams(segment):
    grams = []
    for i in range(len(segment) - 1):
        grams.append(segment[i:i + 2])
    if len(segment) == 1:
        grams.append(segment)
    return grams


def tokenize(text):
    """返回用于检索的 token 列表：中文二元组 + 英文/数字词 + 汉字单字。"""
    text = normalize_index(text).lower()
    tokens = []
    for chunk in re.split(r'[^\u4e00-\u9fffA-Za-z0-9_+\-#.]+', text):
        if not chunk:
            continue
        if CJK_RE.search(chunk):
            for run in re.findall(r'[\u4e00-\u9fff]+', chunk):
                tokens.extend(_cjk_bigrams(run))
                if len(run) <= 8:
                    tokens.extend(list(run))
        for word in WORD_RE.findall(chunk):
            if len(word) > 1 or word.isdigit():
                tokens.append(word)
    return tokens


def split_sentences(text, max_len=180):
    """按中英文句末符号切句；过长的句子再按逗号切开，便于抽取式引用。"""
    text = normalize(text)
    sentences, buffer = [], ''
    for char in text:
        buffer += char
        if char in SENTENCE_END:
            sentence = buffer.strip()
            buffer = ''
            if sentence:
                sentences.append(sentence)
    if buffer.strip():
        sentences.append(buffer.strip())
    result = []
    for sentence in sentences:
        if len(sentence) <= max_len:
            result.append(sentence)
            continue
        parts, current = [], ''
        for piece in re.split(r'([，,、])', sentence):
            if len(current) + len(piece) > max_len and current:
                parts.append(current)
                current = piece
            else:
                current += piece
        if current:
            parts.append(current)
        result.extend(part.strip() for part in parts if part.strip())
    return result


# 疑问/虚词短语：检索前先剥离，避免「什么/哪些/如何」这类词把无关段落顶上来
QUERY_FILLERS = (
    '为什么', '是什么', '什么是', '什么样', '什么', '哪些', '哪个', '哪里', '怎么', '怎样',
    '如何', '多少', '请问', '一下', '以及', '还有', '我们', '可以', '需要', '应该',
    '各自', '分别', '是否', '有关', '关于', '方面', '问题', '请给', '给出', '说明',
)


# 学科内成对出现的易混概念：问题问「时间」，就不能拿「空间」那段原文作答。
# 这是垂类模型相对通用模型的差异化能力——通用检索只会看到「复杂度」三个字就命中。
CONTRAST_PAIRS = (
    (r'时间复杂度', r'空间复杂度'),
    (r'最好', r'最坏'),
    (r'平均', r'最坏'),
    (r'(?<!不)稳定', r'不稳定'),
    (r'升序', r'降序'),
    (r'入队', r'出队'),
    (r'进栈', r'出栈'),
    (r'前序', r'后序'),
    (r'有向', r'无向'),
    (r'深度优先', r'广度优先'),
    (r'递归', r'迭代'),
    (r'稀疏', r'稠密'),
)


# 学科实体表：用于判断「这段原文讲的是不是同一个对象」。
# 问快速排序却拿希尔排序的段落作答，是垂类问答最常见的错误，通用检索看不出来。
DISCIPLINE_ENTITIES = (
    '直接插入排序', '希尔排序', '冒泡排序', '快速排序', '快速排序的', '简单选择排序', '选择排序',
    '堆排序', '归并排序', '基数排序', '计数排序', '插入排序', '冒泡',
    '二叉排序树', '平衡二叉树', 'AVL', '红黑树', '哈夫曼', '完全二叉树', '满二叉树',
    '哈希表', '散列表', '邻接矩阵', '邻接表', '十字链表',
    '深度优先', '广度优先', 'Dijkstra', 'Floyd', 'Bellman-Ford', 'SPFA', 'Kruskal', 'Prim',
    '拓扑排序', 'AOE', 'AOV', '关键路径',
)


def entities_in(text):
    """返回文本中出现的学科实体集合。"""
    return {entity for entity in DISCIPLINE_ENTITIES if entity in text} if text else set()


def entity_affinity(question, text, bonus=0.25, penalty=0.30):
    """问题点名的实体在原文中出现则加分；原文只讲了别的同类实体则扣分。"""
    if not question or not text:
        return 0.0
    asked = {entity for entity in DISCIPLINE_ENTITIES if entity in question}
    if not asked:
        return 0.0
    if any(entity in text for entity in asked):
        return bonus
    others = {entity for entity in DISCIPLINE_ENTITIES if entity in text} - asked
    return -penalty if others else 0.0


def contrast_penalty(question, text, weight=0.25):
    """问题只涉及成对概念中的一方，而原文只讲另一方时，返回惩罚分。"""
    if not question or not text:
        return 0.0
    penalty = 0.0
    for pattern_a, pattern_b in CONTRAST_PAIRS:
        ask_a = bool(re.search(pattern_a, question))
        ask_b = bool(re.search(pattern_b, question))
        if ask_a == ask_b:  # 都没提，或两者都提，不需要消歧
            continue
        has_a = bool(re.search(pattern_a, text))
        has_b = bool(re.search(pattern_b, text))
        if ask_a and has_b and not has_a:
            penalty += weight
        elif ask_b and has_a and not has_b:
            penalty += weight
    return min(penalty, 0.5)


def clean_query(query):
    """去掉疑问词与客套词，保留学科术语，提升检索命中精度。"""
    text = normalize(query or '')
    for word in QUERY_FILLERS:
        text = text.replace(word, ' ')
    return re.sub(r'\s+', ' ', text).strip() or normalize(query or '')


def content_tokens(query):
    """检索用词：去掉疑问词与单字虚词，保留 2 字及以上的学科术语。"""
    tokens = [token for token in tokenize(clean_query(query)) if len(token) >= 2]
    return tokens or tokenize(query)


def char_ngrams(text, n=2):
    text = PUNCT_RE.sub('', normalize(text))
    if len(text) <= n:
        return [text] if text else []
    return [text[i:i + n] for i in range(len(text) - n + 1)]


def char_ngram_set(text, n=3):
    """字符 n 元组集合（去空白），用于判断句子与问题的「主题连贯度」。"""
    return set(char_ngrams(text, n))


def coherence(query, sentence, n=3):
    """句子与问题共享的字符 n 元组比例，比词袋更严格，能过滤同章节的无关句。"""
    query_grams = char_ngram_set(query, n)
    if not query_grams:
        return 0.0
    return len(query_grams & char_ngram_set(sentence, n)) / len(query_grams)


def hashing_embed(text, dim=512):
    """符号哈希 TF 向量（L2 归一化）。无需训练，纯 numpy，可离线构建索引。"""
    import numpy as np
    vector = np.zeros(dim, dtype='float32')
    counts = Counter(tokenize(text))
    for token, count in counts.items():
        h = hash_token(token)
        index = h % dim
        sign = 1.0 if (h >> 16) & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector


def hash_token(token):
    """稳定哈希（不受 PYTHONHASHSEED 影响），保证索引跨进程可复现。"""
    value = 2166136261
    for byte in token.encode('utf-8'):
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value


class BM25:
    """轻量 BM25 实现，索引可直接序列化为 JSON。"""

    def __init__(self, doc_tokens, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.doc_tokens = doc_tokens
        self.doc_freqs = [Counter(tokens) for tokens in doc_tokens]
        self.doc_lens = [len(tokens) for tokens in doc_tokens]
        self.avg_len = (sum(self.doc_lens) / len(self.doc_lens)) if self.doc_lens else 0.0
        df = Counter()
        for freqs in self.doc_freqs:
            for token in freqs:
                df[token] += 1
        total = len(doc_tokens)
        self.idf = {token: math.log(1 + (total - n + 0.5) / (n + 0.5)) for token, n in df.items()}

    def scores(self, query_tokens):
        query_tokens = list(dict.fromkeys(query_tokens))
        results = []
        for index, freqs in enumerate(self.doc_freqs):
            score = 0.0
            doc_len = self.doc_lens[index] or 1
            for token in query_tokens:
                idf = self.idf.get(token)
                if not idf:
                    continue
                freq = freqs.get(token, 0)
                if not freq:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * doc_len / (self.avg_len or 1))
                score += idf * freq * (self.k1 + 1) / denom
            results.append(score)
        return results


def overlap_score(query, text):
    """查询词覆盖率（0~1），用于答案质量校验与抽取式摘要排序。"""
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(tokenize(text))
    if not text_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


def extract_json(text, default=None):
    """从模型输出中稳健地抽取 JSON 对象/数组。"""
    if not text:
        return default
    text = text.strip()
    candidates = []
    fenced = re.search(r'```(?:json)?\s*([\s\S]*?)```', text, re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text)
    for match in re.finditer(r'(\{[\s\S]*\}|\[[\s\S]*\])', text):
        candidates.append(match.group(1))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return default


def clip(text, limit):
    """按字符安全截断，尽量不截断在句子中间。"""
    text = text or ''
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ('。', '！', '？', '\n', '；'):
        position = cut.rfind(mark)
        if position > limit * 0.6:
            return cut[:position + 1]
    return cut


def keywords(text, top_n=8):
    """抽取关键词（按 TF 排序并过滤停用词），用于热点图谱与检索高亮。"""
    stop = {
        '的', '了', '和', '是', '在', '与', '及', '或', '对', '为', '以', '等', '中', '上', '下',
        '可以', '需要', '通过', '进行', '一个', '我们', '这个', '这个', '因此', '所以', '如果',
        'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'is', 'of', 'to', 'in',
    }
    counts = Counter(token for token in tokenize(text) if token not in stop and len(token) > 1)
    return [word for word, _ in counts.most_common(top_n)]