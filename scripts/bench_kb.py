"""知识库检索压测：对比不同规模下的检索耗时（Python 预计算索引 / C++ 内核）。

用法：
    python scripts/bench_kb.py                 # 默认规模 81 ~ 48600
    python scripts/bench_kb.py --scale 81,810  # 指定规模
    python scripts/bench_kb.py --engine cpp    # 只测 C++ 内核
    python scripts/bench_kb.py --engine python # 只测 Python 引擎

输出：每个规模下的索引构建耗时、检索 p50/p95、QPS，以及相对规模 81 的倍数。
"""
import argparse
import copy
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

import numpy as np

import kb_service

QUERIES = [
    'Dijkstra 算法为什么不能处理负权边',
    '快速排序的最坏时间复杂度是多少',
    '二叉树的中序非递归遍历怎么写',
    '栈和队列的区别是什么',
    '哈希表冲突怎么解决',
]


def build_scale(kb, base_chunks, base_vectors, scale):
    """把知识块按倍数复制，模拟学科级知识库规模。"""
    chunks = []
    for copy_index in range(scale):
        for chunk in base_chunks:
            item = copy.copy(chunk)
            if copy_index:
                item['text'] = (chunk.get('text') or '') + f'（副本 {copy_index}）'
                item['index'] = chunk.get('index', 0) + copy_index * len(base_chunks)
            chunks.append(item)
    vectors = np.tile(base_vectors, (scale, 1)) if base_vectors is not None and base_vectors.size else None
    kb.chunks = chunks
    kb.vectors = vectors
    kb._index = None
    return chunks


def measure(kb, queries, rounds=3):
    times = []
    for _ in range(rounds):
        for query in queries:
            started = time.perf_counter()
            kb.search(query, top_k=5)
            times.append((time.perf_counter() - started) * 1000)
    times.sort()
    return times[len(times) // 2], times[int(len(times) * 0.95) - 1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scale', default='1,10,60,200,600')
    parser.add_argument('--engine', default='auto', choices=['auto', 'python', 'cpp'])
    args = parser.parse_args()
    if args.engine != 'auto':
        os.environ['KB_ENGINE'] = args.engine

    kb = kb_service.get_kb()
    kb.ensure()
    base_chunks = list(kb.chunks)
    base_vectors = kb.vectors
    total = len(base_chunks)
    print(f'基础知识库：{total} 块；多轮测 p50/p95（每规模 5 题 × 3 轮）\n')
    print(f'{"知识块":>8}  {"索引构建":>10}  {"检索p50":>9}  {"检索p95":>9}  {"QPS":>8}  {"引擎":>8}')

    for scale in [int(item) for item in args.scale.split(',') if item.strip()]:
        build_scale(kb, base_chunks, base_vectors, scale)
        started = time.perf_counter()
        kb._ensure_index()
        build_ms = (time.perf_counter() - started) * 1000
        p50, p95 = measure(kb, QUERIES)
        engine = getattr(kb._ensure_index(), 'engine', '?')
        print(f'{len(kb.chunks):>8}  {build_ms:>8.0f} ms  {p50:>7.1f} ms  {p95:>7.1f} ms  {1000.0 / max(p50, 1e-9):>7.0f}  {engine:>8}')


if __name__ == '__main__':
    main()