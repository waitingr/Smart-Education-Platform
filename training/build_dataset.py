"""构建学科指令微调数据集。

数据来源：
  1. training/data/qa_seed.jsonl —— 人工撰写的「学科问答」种子对（带出处标注）；
  2. data/knowledge_base_docs/ —— 学科知识库，按章节自动生成「讲解类」指令。

输出（Alpaca 风格，可直接用于 SFT）：
  training/data/train.jsonl
  training/data/val.jsonl
每条记录字段：instruction / input / output / meta

用法：
    python training/build_dataset.py
    python training/build_dataset.py --no-kb      # 只用种子对
    python training/build_dataset.py --max-kb 200 # 控制由知识库生成的上限
"""
import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))

TEMPLATES = [
    ('请讲解「{topic}」的核心要点，并指出常见误区。', '讲解'),
    ('在学习数据结构与算法时，「{topic}」这一部分需要掌握什么？', '讲解'),
    ('用一句话概括「{topic}」，再展开说明理由。', '概括'),
]


def load_seed(path):
    records = []
    if not os.path.isfile(path):
        return records
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            records.append({
                'instruction': item['instruction'],
                'input': item.get('input', ''),
                'output': item['output'],
                'meta': {'source': 'seed', 'has_citation': '（出处：' in item['output']},
            })
    return records


def load_from_kb(limit):
    """把知识库章节转成「讲解类」指令样本：问题由章节标题构造，答案取章节正文。"""
    try:
        from kb_service import get_kb
    except Exception as error:  # 依赖缺失时跳过
        print(f'[跳过] 无法加载知识库：{error}')
        return []

    kb = get_kb()
    records, seen = [], set()
    for chunk in kb.chunks:
        section = (chunk.get('section') or '').strip()
        text = (chunk.get('text') or '').strip()
        if not section or len(text) < 120:
            continue
        key = (chunk.get('doc_title'), section)
        if key in seen:
            continue
        seen.add(key)
        instruction, kind = random.Random(hash(section) & 0xFFFF).choice(TEMPLATES)
        topic = section.split('>')[-1].strip() if '>' in section else section
        answer = (
            f'{text}\n\n'
            f'（出处：《{chunk.get("doc_title")}》·{topic}）'
        )
        records.append({
            'instruction': instruction.format(topic=topic),
            'input': '',
            'output': answer,
            'meta': {
                'source': 'kb',
                'kind': kind,
                'doc_title': chunk.get('doc_title'),
                'section': section,
                'source_type': chunk.get('source_type'),
            },
        })
        if limit and len(records) >= limit:
            break
    return records


def main():
    parser = argparse.ArgumentParser(description='构建学科指令微调数据集')
    parser.add_argument('--seed', default=os.path.join(ROOT, 'training', 'data', 'qa_seed.jsonl'))
    parser.add_argument('--out-dir', default=os.path.join(ROOT, 'training', 'data'))
    parser.add_argument('--no-kb', action='store_true', help='不使用知识库自动生成样本')
    parser.add_argument('--max-kb', type=int, default=400, help='知识库样本上限')
    parser.add_argument('--val-ratio', type=float, default=0.08)
    parser.add_argument('--seed-value', type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed_value)
    records = load_seed(args.seed)
    print(f'种子问答对：{len(records)} 条')
    if not args.no_kb:
        kb_records = load_from_kb(args.max_kb)
        print(f'知识库生成样本：{len(kb_records)} 条')
        records.extend(kb_records)

    if not records:
        print('没有可用样本，退出。')
        return 1

    # 去重（同一问题只保留一条）
    deduped, seen = [], set()
    for item in records:
        key = item['instruction'] + '|' + item['input']
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    records = deduped
    random.shuffle(records)

    split = max(1, int(len(records) * args.val_ratio))
    val, train = records[:split], records[split:]

    os.makedirs(args.out_dir, exist_ok=True)
    for name, rows in (('train.jsonl', train), ('val.jsonl', val)):
        path = os.path.join(args.out_dir, name)
        with open(path, 'w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f'已写出 {name}：{len(rows)} 条 → {path}')

    print(f'合计 {len(records)} 条样本，含引用标注 {sum(1 for r in records if r["meta"].get("has_citation"))} 条')
    return 0


if __name__ == '__main__':
    sys.exit(main())