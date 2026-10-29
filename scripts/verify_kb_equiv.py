"""检索引擎等价性校验：新版（预计算索引 / C++ 内核）必须和原实现在 top-k 上完全一致。

做法：把改动前的 search() 打分逻辑原样抄成 reference_search()，与当前 kb.search()
在同一个知识库上逐题比对：命中的 chunk_id 顺序、score（4 位小数）、coverage，
任何一处不同都算失败——检索结果变了，引用就会变，36 项自检可能悄悄失效。

用法：
    python scripts/verify_kb_equiv.py            # 默认 30 题
    python scripts/verify_kb_equiv.py --engine cpp
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

import numpy as np

import kb_service
from config import Config
from text_utils import (BM25, content_tokens, contrast_penalty, entity_affinity,
                        overlap_score, tokenize)

QUERIES = [
    # 评测集里的 12 题
    'Dijkstra 算法为什么不能处理负权边',
    '快速排序的最坏时间复杂度是多少',
    '二叉树的中序非递归遍历怎么写',
    '栈和队列的区别是什么',
    '哈希表冲突怎么解决',
    '什么是拓扑排序',
    '希尔排序和直接插入排序的关系',
    '关键路径怎么求',
    '二叉排序树的查找效率',
    '图的深度优先遍历和广度优先遍历有什么区别',
    '冒泡排序为什么是稳定的',
    '哈夫曼编码的构造过程',
    # 成对概念 / 实体消歧 / 短语命中
    '时间复杂度怎么算',
    '空间复杂度怎么算',
    '稳定排序有哪些',
    '不稳定排序有哪些',
    '快速排序的空间复杂度',
    '归并排序的合并过程',
    '邻接矩阵和邻接表的区别',
    '平衡二叉树怎么调整',
    'AOE 网的关键路径',
    'Kruskal 和 Prim 的区别',
    '单源最短路径 Bellman-Ford',
    '堆排序的下沉调整',
    '静态链表',
    '折半查找的判定树',
    '队列的入队出队操作',
    '循环队列判空判满',
    '递归和迭代哪个快',
    '稀疏矩阵的压缩存储',
]


def reference_search(kb, query, top_k=None, prefer_types=None, authority_weight=0.1):
    """改动前的实现，原样保留作为基准。"""
    kb.ensure()
    top_k = top_k or Config.KB_TOP_K
    if not kb.chunks:
        return []
    query = (query or '').strip()
    if not query:
        return []
    bm25 = BM25([tokenize(chunk['text']) for chunk in kb.chunks])
    search_tokens = content_tokens(query)
    lexical = np.asarray(bm25.scores(search_tokens), dtype='float32')
    if lexical.size and lexical.max() > 0:
        lexical = lexical / lexical.max()
    if kb.vectors is not None and len(kb.vectors) == len(kb.chunks) and kb.vectors.size:
        query_vector = kb_service.embedder.encode([query])[0]
        semantic = kb.vectors @ query_vector
        semantic = np.maximum(semantic, 0.0)
        if semantic.max() > 0:
            semantic = semantic / semantic.max()
        semantic_weight = 0.25 if (kb.meta or {}).get('backend', 'hash') == 'hash' else 0.55
        combined = semantic_weight * semantic + (1.0 - semantic_weight) * lexical
    else:
        combined = lexical

    query_tokens = set(search_tokens)
    query_text = ' '.join(search_tokens)
    query_phrases = {query_text[i:i + 12] for i in range(max(0, len(query_text) - 11))}
    prefer_types = set(prefer_types or [])
    scored = []
    for position, base in enumerate(combined):
        chunk = kb.chunks[position]
        score = float(base)
        title_tokens = set(tokenize(chunk['doc_title'] + ' ' + chunk['section']))
        if query_tokens:
            hit = len(query_tokens & title_tokens) / len(query_tokens)
            score += 0.18 * hit
        chunk_tokens = set(tokenize(chunk['text']))
        if query_tokens:
            score += 0.55 * (len(query_tokens & chunk_tokens) / len(query_tokens))
        if query_phrases:
            chunk_text = ' '.join(tokenize(chunk['text']))
            if any(phrase and phrase in chunk_text for phrase in query_phrases):
                score += 0.15
        if chunk['source_type'] in prefer_types:
            score += 0.08
        score -= contrast_penalty(query, chunk['text'], weight=0.20)
        score += entity_affinity(query, chunk['text'], bonus=0.20, penalty=0.20)
        score *= (1.0 - authority_weight * (int(chunk.get('authority') or 3) - 1))
        scored.append((score, position))
    scored.sort(key=lambda item: item[0], reverse=True)

    results = []
    for rank, (score, position) in enumerate(scored[:top_k], start=1):
        chunk = kb.chunks[position]
        coverage = overlap_score(query, chunk['text'])
        results.append({
            'index': rank,
            'chunk_id': chunk.get('index'),
            'score': round(min(score, 1.5), 4),
            'coverage': round(coverage, 3),
            'doc_title': chunk['doc_title'],
            'section': chunk['section'],
        })
    return results


def main():
    kb = kb_service.get_kb()
    kb.ensure()
    index = kb._ensure_index()
    engine = getattr(index, 'engine', type(index).__name__)
    print(f'知识块 {len(kb.chunks)} 个，检索引擎 {engine}，比对 {len(QUERIES)} 条查询')
    if os.getenv('KB_ENGINE', '').lower() == 'cpp' and engine != 'cpp':
        print(f'❌ 指定了 KB_ENGINE=cpp，但实际引擎是 {engine}：{getattr(index, "engine_error", "")}')
        return 1

    failures = []
    for query in QUERIES:
        reference = reference_search(kb, query, top_k=10)
        actual = kb.search(query, top_k=10)
        if len(reference) != len(actual):
            failures.append((query, '数量不同', len(reference), len(actual)))
            continue
        for left, right in zip(reference, actual):
            if left['chunk_id'] != right['chunk_id']:
                failures.append((query, f"第 {left['index']} 名不同", left['doc_title'], right['doc_title']))
                break
            if abs(left['score'] - right['score']) > 5e-4:
                failures.append((query, f"第 {left['index']} 名分数不同", left['score'], right['score']))
                break
            if abs(left['coverage'] - right['coverage']) > 5e-4:
                failures.append((query, f"第 {left['index']} 名覆盖率不同", left['coverage'], right['coverage']))
                break

    if failures:
        print(f'\n❌ {len(failures)} 条查询结果不一致：')
        for item in failures[:10]:
            print('   ', item)
        return 1
    print(f'\n✅ {len(QUERIES)} 条查询 top-10 与改动前实现完全一致（chunk_id / score / coverage）')
    return 0


if __name__ == '__main__':
    sys.exit(main())