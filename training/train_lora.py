"""学科 LoRA 微调脚本（QLoRA / SFT），基座权重需自行下载。

设计要点：
  * 全部重依赖（torch / transformers / peft / datasets）延迟导入，
    未安装时给出明确提示，不影响平台本体运行；
  * 使用 Qwen 官方 chat template，只对 assistant 段计算 loss（mask 掉问题部分），
    避免模型学会复述问题；
  * 默认 LoRA（r=16, alpha=32），4bit 量化（CUDA 且装了 bitsandbytes 时自动启用），
    使得单张 12GB 显存的消费级显卡也能跑 7B/8B 模型；
  * 训练结束保存 adapter，并可选择合并权重（--merge）导出完整模型。
  * **本仓库不再内置基座权重**（14.5 GB 的纯文本权重已移除，平台默认直接用 Ollama 的
    `qwen3-vl:8b`）。需要微调时请先下载基座，并用 `--base` 显式指定目录。

用法（基座需自行下载）：
    python training/build_dataset.py
    # 与平台默认模型一致的视觉基座
    huggingface-cli download Qwen/Qwen3-VL-8B-Instruct --local-dir models/llm/Qwen3-VL-8B-Instruct
    python training/train_lora.py --base models/llm/Qwen3-VL-8B-Instruct \\
        --train training/data/train.jsonl --val training/data/val.jsonl \\
        --out training/output/discipline-lora --epochs 3
    python training/train_lora.py --merge --base models/llm/Qwen3-VL-8B-Instruct \\
        --adapter training/output/discipline-lora
"""
import argparse
import json
import os
import sys


def require(name, hint=''):
    try:
        return __import__(name)
    except ImportError:
        print(f'[缺少依赖] {name} 未安装。' + (hint or '请执行：pip install torch transformers peft datasets accelerate'))
        raise SystemExit(2)


def read_jsonl(path):
    rows = []
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_example(tokenizer, item, max_length):
    """拼成 chat 模板，并对 user 段打 -100 mask，只学 assistant 输出。"""
    import torch

    user_text = item['instruction'] + (('\n' + item['input']) if item.get('input') else '')
    prompt_ids = tokenizer.apply_chat_template(
        [{'role': 'system', 'content': '你是计算机科学与技术学科的助教，回答基于课程知识库并标注出处。'},
         {'role': 'user', 'content': user_text}],
        add_generation_prompt=True, tokenize=True, return_dict=False,
    )
    answer_ids = tokenizer(item['output'] + tokenizer.eos_token, add_special_tokens=False)
    input_ids = list(prompt_ids) + list(answer_ids['input_ids'])
    labels = [-100] * len(prompt_ids) + list(answer_ids['input_ids'])
    if len(input_ids) > max_length:
        input_ids = input_ids[:max_length]
        labels = labels[:max_length]
    attention = [1] * len(input_ids)
    return {'input_ids': torch.tensor(input_ids), 'labels': torch.tensor(labels),
            'attention_mask': torch.tensor(attention)}


def collate(batch, pad_id):
    import torch

    length = max(item['input_ids'].size(0) for item in batch)
    input_ids = torch.full((len(batch), length), pad_id, dtype=torch.long)
    labels = torch.full((len(batch), length), -100, dtype=torch.long)
    attention = torch.zeros((len(batch), length), dtype=torch.long)
    for row, item in enumerate(batch):
        size = item['input_ids'].size(0)
        input_ids[row, :size] = item['input_ids']
        labels[row, :size] = item['labels']
        attention[row, :size] = item['attention_mask']
    return {'input_ids': input_ids, 'labels': labels, 'attention_mask': attention}


def main():
    parser = argparse.ArgumentParser(description='学科 LoRA 微调')
    parser.add_argument('--base', required=True,
                        help='基座权重目录（HuggingFace 格式）。本仓库不再内置权重，需自行下载：'
                             '推荐 Qwen/Qwen3-VL-8B-Instruct（与平台默认模型一致，可识图），'
                             '纯文本微调也可用 Qwen/Qwen2.5-7B-Instruct')
    parser.add_argument('--train', default='training/data/train.jsonl')
    parser.add_argument('--val', default='training/data/val.jsonl')
    parser.add_argument('--out', default='training/output/discipline-lora')
    parser.add_argument('--epochs', type=float, default=3.0)
    parser.add_argument('--batch-size', type=int, default=1)
    parser.add_argument('--grad-accum', type=int, default=8)
    parser.add_argument('--lr', type=float, default=1e-4)
    parser.add_argument('--max-length', type=int, default=1536)
    parser.add_argument('--lora-r', type=int, default=16)
    parser.add_argument('--lora-alpha', type=int, default=32)
    parser.add_argument('--lora-dropout', type=float, default=0.05)
    parser.add_argument('--no-4bit', action='store_true')
    parser.add_argument('--merge', action='store_true', help='把 adapter 合并进基座并导出完整模型')
    parser.add_argument('--adapter', default='', help='--merge 时的 adapter 路径')
    args = parser.parse_args()

    torch = require('torch')
    transformers = require('transformers', '请执行：pip install transformers accelerate')
    peft = require('peft', '请执行：pip install peft')

    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                              TrainingArguments, default_data_collator)

    # ---------------- 合并权重模式 ----------------
    if args.merge:
        adapter = args.adapter or args.out
        print(f'合并 LoRA：{adapter} + {args.base}')
        tokenizer = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype='auto', trust_remote_code=True)
        merged = peft.PeftModel.from_pretrained(model, adapter).merge_and_unload()
        target = os.path.join(adapter, 'merged')
        merged.save_pretrained(target, safe_serialization=True)
        tokenizer.save_pretrained(target)
        print(f'已导出合并后的完整模型：{target}')
        return 0

    if not os.path.isfile(args.train):
        print(f'训练集不存在：{args.train}\n请先执行 python training/build_dataset.py')
        return 1

    tokenizer = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    use_4bit = not args.no_4bit and torch.cuda.is_available()
    quant_config = None
    if use_4bit:
        try:
            from transformers import BitsAndBytesConfig
            quant_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type='nf4',
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )
            print('已启用 4bit 量化加载（QLoRA）')
        except Exception as error:
            print(f'4bit 不可用（{error}），改为普通精度加载')
            use_4bit = False

    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map='auto' if torch.cuda.is_available() else None,
        quantization_config=quant_config,
        trust_remote_code=True,
    )
    model.config.use_cache = False

    if use_4bit:
        try:
            from peft import prepare_model_for_kbit_training
            model = prepare_model_for_kbit_training(model)
        except Exception:
            pass

    lora_config = peft.LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias='none',
        task_type='CAUSAL_LM',
        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'],
    )
    model = peft.get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    train_rows = read_jsonl(args.train)
    val_rows = read_jsonl(args.val) if os.path.isfile(args.val) else []
    print(f'训练样本 {len(train_rows)} 条，验证样本 {len(val_rows)} 条')

    train_set = [build_example(tokenizer, item, args.max_length) for item in train_rows]
    val_set = [build_example(tokenizer, item, args.max_length) for item in val_rows]

    use_cuda = torch.cuda.is_available()
    training_args = TrainingArguments(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type='cosine',
        warmup_ratio=0.03,
        logging_steps=5,
        save_strategy='epoch',
        save_total_limit=2,
        fp16=use_cuda,
        gradient_checkpointing=use_cuda,
        report_to=[],
        remove_unused_columns=False,
    )

    def collator(features):
        return collate(features, tokenizer.pad_token_id)

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_set,
        eval_dataset=val_set or None,
        data_collator=collator,
    )
    trainer.train()
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f'adapter 已保存：{args.out}')

    metrics = trainer.evaluate() if val_set else {}
    if metrics:
        with open(os.path.join(args.out, 'eval_metrics.json'), 'w', encoding='utf-8') as handle:
            json.dump({key: float(value) for key, value in metrics.items() if isinstance(value, (int, float))},
                      handle, ensure_ascii=False, indent=2)
        print('验证指标：', {key: round(float(value), 4) for key, value in metrics.items() if isinstance(value, float)})

    print('\n完成。把 adapter 合并为完整模型后即可被平台直接加载：')
    print(f'  python training/train_lora.py --merge --adapter {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())