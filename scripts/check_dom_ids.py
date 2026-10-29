"""静态检查：前端 JS 里 getElementById 引用的 id 是否都在 HTML 或由 JS 动态生成。"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

JS_DIR = 'frontend/js'
html = open('frontend/index.html', encoding='utf-8').read()
js_files = sorted(name for name in os.listdir(JS_DIR) if name.endswith('.js'))
scripts = {name: open(os.path.join(JS_DIR, name), encoding='utf-8').read() for name in js_files}
js = '\n'.join(scripts.values())

need = ['ws-learn', 'ws-teach', 'ws-research', 'profile-root', 'kb-root', 'analytics-root',
        'chat-welcome', 'new-chat-btn', 'mode-qa', 'mode-code', 'mode-diagnosis', 'history-list',
        'page-course', 'lms-root', 'model-version-display', 'vision-capability-display']
print('missing required ids:', [item for item in need if 'id="%s"' % item not in html])

referenced = set(re.findall(r"getElementById\(['\"]([A-Za-z0-9_-]+)['\"]\)", js))
dynamic = set(re.findall(r'id="([A-Za-z0-9_-]+)"', js))
dynamic |= set(re.findall(r"id='([A-Za-z0-9_-]+)'", js))
dynamic |= set(re.findall(r'id=\"([A-Za-z0-9_-]+)\"', js))
# 模板字符串里用 + 拼出来的 id 前缀（如 'lms-' + x）无法静态确定，按前缀白名单豁免
prefix_whitelist = ('lms-', 'page-', 'ws-', 'chat-', 'kb-', 'analytics-', 'profile-', 'homework-',
                    'video-', 'material-', 'student-', 'auth-', 'settings-', 'submission-', 'grade-')
undefined = sorted(item for item in referenced
                   if 'id="%s"' % item not in html and item not in dynamic
                   and not item.startswith(prefix_whitelist))
print('referenced-but-undefined ids:', undefined)
print('checked js files:', js_files)