"""用本机 Ollama 视觉模型识别图片，验证 VLM 链路是否真的可用。

用法：
    python scripts/vision_probe.py <图片路径> [问题]
"""
import base64
import json
import os
import sys
import time
import urllib.request

HOST = os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434').rstrip('/')
MODEL = os.getenv('VISION_MODEL', 'qwen3-vl:8b')
PROMPT = sys.argv[2] if len(sys.argv) > 2 else (
    '请用简体中文描述这张幻灯片：标题是什么、有几个分块、每个分块的标题和要点分别是什么。'
    '如果有文字重叠、超出边界或被裁切的地方，请明确指出。'
)


def main():
    path = sys.argv[1]
    with open(path, 'rb') as handle:
        encoded = base64.b64encode(handle.read()).decode('ascii')
    payload = {
        'model': MODEL,
        'prompt': PROMPT,
        'images': [encoded],
        'stream': False,
        'options': {'num_ctx': 8192, 'temperature': 0.2},
    }
    request = urllib.request.Request(
        HOST + '/api/generate',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.loads(response.read().decode('utf-8'))
    elapsed = time.time() - started
    eval_count = data.get('eval_count') or 0
    print(f'模型 {data.get("model")} ｜ 耗时 {elapsed:.1f}s ｜ 生成 {eval_count} tokens'
          f' ｜ {(eval_count / elapsed) if elapsed else 0:.1f} tok/s')
    print('-' * 60)
    print(data.get('response', '').strip())


if __name__ == '__main__':
    main()