"""视觉模型（VLM）客户端：把图片变成可检索、可引用的文字。

默认走本机 Ollama（`VISION_PROVIDER=ollama`，模型 `qwen3-vl:8b`），
也支持任意 OpenAI 兼容的视觉接口（`VISION_PROVIDER=openai`，配合 base_url 与 key）。

设计原则与 LLM 侧一致：**能力不可用时明确降级**，返回空文本并给出原因，
绝不假装看清了图片。调用方据此决定是提示用户还是跳过。
"""
import base64
import io
import json
import os
import time
import urllib.error
import urllib.request

from config import Config

# 探测结果缓存（避免每次请求都打一次 /api/tags）
_PROBE = {'at': 0.0, 'ok': False, 'detail': ''}
_PROBE_TTL = 60.0


def _shrink(path, max_side):
    """把图片压到视觉模型能接受的尺寸，返回 (bytes, mime)。失败则原样返回。"""
    try:
        from PIL import Image
        with Image.open(path) as image:
            image = image.convert('RGB')
            width, height = image.size
            scale = min(1.0, max_side / max(width, height))
            if scale < 1.0:
                image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=88)
            return buffer.getvalue(), 'image/jpeg'
    except Exception:
        with open(path, 'rb') as handle:
            return handle.read(), 'image/png'


def _post_json(url, payload, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def image_data_url(image_path, max_side=None):
    """把图片压成 data URL，供「生成模型直接看图」使用；失败返回空串。"""
    if not image_path or not os.path.exists(image_path):
        return ''
    raw, mime = _shrink(image_path, max_side or Config.VISION_MAX_IMAGE_SIDE)
    return f'data:{mime};base64,{base64.b64encode(raw).decode("ascii")}'


class VisionClient:
    def __init__(self):
        self.provider = Config.VISION_PROVIDER
        self.base_url = Config.VISION_BASE_URL
        self.model = Config.VISION_MODEL
        self.timeout = Config.VISION_TIMEOUT

    # ---------------- 可用性 ----------------
    def _resolve(self):
        """auto 模式下：本机 Ollama 可达就选它，否则关闭。"""
        if self.provider == 'off':
            return 'off'
        if self.provider == 'auto':
            return 'ollama' if self._ollama_up() else 'off'
        return self.provider

    def _ollama_up(self):
        global _PROBE
        now = time.time()
        if now - _PROBE['at'] < _PROBE_TTL:
            return _PROBE['ok']
        ok, detail = False, ''
        try:
            with urllib.request.urlopen(self.base_url + '/api/tags', timeout=3) as response:
                data = json.loads(response.read().decode('utf-8'))
            names = [item.get('name', '') for item in data.get('models', [])]
            ok = any(name.split(':')[0] == self.model.split(':')[0] for name in names)
            detail = '' if ok else f'Ollama 已运行，但未找到模型 {self.model}（已安装：{", ".join(names) or "无"}）'
        except Exception as error:
            detail = f'无法连接本机 Ollama（{type(error).__name__}）：{self.base_url}'
        _PROBE.update({'at': now, 'ok': ok, 'detail': detail})
        return ok

    @property
    def available(self):
        if self._resolve() == 'off':
            return False
        if self._resolve() == 'ollama':
            return self._ollama_up()
        return True

    def health(self):
        resolved = self._resolve()
        if resolved == 'off':
            return {
                'available': False,
                'provider': 'off',
                'model': self.model,
                'notice': _PROBE['detail'] or '未配置视觉模型（图片仅登记元数据，不做内容识别）',
            }
        ok = self.available
        return {
            'available': ok,
            'provider': resolved,
            'model': self.model,
            'endpoint': self.base_url,
            'notice': '' if ok else (_PROBE['detail'] or '视觉模型不可用'),
        }

    # ---------------- 主流程 ----------------
    def describe(self, image_path, prompt=None):
        """返回 (描述文本, 元信息)。失败时文本为空字符串，原因写在 meta['error']。"""
        prompt = prompt or Config.VISION_PROMPT
        if not os.path.exists(image_path):
            return '', {'error': '图片不存在', 'provider': self.provider}
        if not self.available:
            return '', {'error': _PROBE['detail'] or '视觉模型不可用', 'provider': self.provider}

        started = time.time()
        raw, mime = _shrink(image_path, Config.VISION_MAX_IMAGE_SIDE)
        encoded = base64.b64encode(raw).decode('ascii')
        resolved = self._resolve()

        try:
            if resolved == 'ollama':
                data = _post_json(
                    self.base_url + '/api/generate',
                    {
                        'model': self.model,
                        'prompt': prompt,
                        'images': [encoded],
                        'stream': False,
                        # 常驻显存：单机 8B 冷启动要 ~20 秒，演示/批处理时不该反复付这笔钱
                        'keep_alive': getattr(Config, 'VISION_KEEP_ALIVE', '30m') or '30m',
                    },
                    self.timeout,
                )
                text = (data.get('response') or '').strip()
            else:
                data = _post_json(
                    self.base_url.rstrip('/') + '/chat/completions',
                    {
                        'model': self.model,
                        'messages': [{
                            'role': 'user',
                            'content': [
                                {'type': 'text', 'text': prompt},
                                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}},
                            ],
                        }],
                        'temperature': 0.2,
                    },
                    self.timeout,
                )
                text = (data['choices'][0]['message']['content'] or '').strip()
        except urllib.error.HTTPError as error:
            return '', {'error': f'视觉模型返回 {error.code}', 'provider': resolved, 'model': self.model}
        except Exception as error:
            return '', {'error': f'{type(error).__name__}: {error}', 'provider': resolved, 'model': self.model}

        return text, {
            'provider': resolved,
            'model': self.model,
            'latency_ms': round((time.time() - started) * 1000),
            'image_bytes': len(raw),
        }


_CLIENT = None


def get_vision():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = VisionClient()
    return _CLIENT