"""静态检查：前端模板里 onclick 调用的函数是否都在 JS 中定义（防手写拼接漏函数）。"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

JS_DIR = 'frontend/js'
html = open('frontend/index.html', encoding='utf-8').read()
scripts = {name: open(os.path.join(JS_DIR, name), encoding='utf-8').read()
           for name in sorted(os.listdir(JS_DIR)) if name.endswith('.js')}
source = html + '\n' + '\n'.join(scripts.values())

# onclick="foo(...)" —— 只看每条语句开头的函数名，避免把 .remove() 之类方法误判成函数
called = set()
for match in re.finditer(r'onclick="([^"]+)"', source):
    for statement in match.group(1).split(';'):
        found = re.match(r'\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\(', statement)
        if found:
            called.add(found.group(1))

defined = set(re.findall(r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)', source))
defined |= set(re.findall(r'\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:function|\()', source))
defined |= set(re.findall(r'window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=', source))

builtin = {'event', 'this', 'preventDefault', 'alert', 'confirm', 'prompt', 'parseInt', 'parseFloat',
           'encodeURIComponent', 'setTimeout', 'clearTimeout', 'String', 'Number', 'Boolean', 'Array',
           'Object', 'Math', 'JSON', 'Date', 'fetch', 'if', 'for', 'while', 'switch', 'return'}
missing = sorted(name for name in called if name not in defined and name not in builtin)
print('onclick 调用但未定义的函数:', missing)
print('检查脚本:', sorted(scripts))