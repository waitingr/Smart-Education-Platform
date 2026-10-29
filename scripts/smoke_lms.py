r"""课程空间（学习通式教学闭环）接口自检。

覆盖：鉴权、课程/章节/任务点、测验提交与评分、讨论区 AI 助教、签到、通知、班级统计、AI 导读/出题/学情建议。

用法：
  .\.venv\Scripts\python.exe scripts\smoke_lms.py           # 全部检查（含 AI，本机 8B 约 3~4 分钟）
  .\.venv\Scripts\python.exe scripts\smoke_lms.py --fast    # 跳过 AI 生成类检查，只验数据链路
  .\.venv\Scripts\python.exe scripts\smoke_lms.py http://127.0.0.1:8000   # 指定服务地址
"""
import json
import sys
import time
import urllib.error
import urllib.request

_FAST = '--fast' in sys.argv
_ARGS = [item for item in sys.argv[1:] if not item.startswith('-')]
BASE = (_ARGS[0] if _ARGS else 'http://127.0.0.1:5000').rstrip('/')
API = BASE + '/api/v1'
TOKEN = {'value': None}
PASSED, FAILED, SKIPPED = [], [], []


def call(method, path, payload=None, auth=True, timeout=900):
    headers = {'Content-Type': 'application/json'}
    if auth and TOKEN['value']:
        headers['Authorization'] = 'Bearer ' + TOKEN['value']
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode('utf-8'))
        except Exception:
            return error.code, {}
    except Exception as error:
        return 0, {'message': f'请求异常：{error}'}


def check(name, condition, detail=''):
    if condition:
        PASSED.append(name)
        print(f'  [PASS] {name}' + (f' — {detail}' if detail else ''))
    else:
        FAILED.append(f'{name}: {detail}')
        print(f'  [FAIL] {name} — {detail}')


def skip(name, reason):
    SKIPPED.append(name)
    print(f'  [SKIP] {name} — {reason}')


def login(identifier, password, role):
    status, body = call('POST', '/auth/login', {'username': identifier, 'password': password, 'role': role}, auth=False)
    token = (body.get('data') or {}).get('api_token') or ''
    if token:
        TOKEN['value'] = token
    return status, token


def section(title):
    print(f'\n=== {title} ===')


def main():
    fast = '--fast' in sys.argv

    section('1. 鉴权与课程列表')
    status, body = call('GET', '/lms/courses', auth=False)
    check('未登录访问被拒绝', status == 401, f'status={status}')
    status, token = login('student2023010101@smartedu.cn', 'Student@123', 'student')
    check('学生登录', status == 200 and bool(token))
    status, body = call('GET', '/lms/courses')
    items = (body.get('data') or {}).get('items') or []
    check('课程列表可读', status == 200 and len(items) >= 1, f'{len(items)} 门课')
    course = items[0] if items else {}
    course_id = course.get('id')
    check('课程带任务点统计', course.get('task_total', 0) > 0,
          f"任务点 {course.get('task_done')}/{course.get('task_total')}")

    section('2. 章节任务点与进度')
    status, body = call('GET', f'/lms/courses/{course_id}')
    chapters = (body.get('data') or {}).get('chapters') or []
    check('课程详情含章节', status == 200 and len(chapters) >= 5, f'{len(chapters)} 章')
    target = next((c for c in chapters if c.get('is_task_point')), None)
    if target:
        # 先重置为未完成、再标记完成，保证这项检查每次都真的跑了一遍状态流转
        call('POST', f"/lms/chapters/{target['id']}/progress", {'status': 'todo', 'minutes': 0})
        status, body = call('POST', f"/lms/chapters/{target['id']}/progress", {'status': 'done', 'minutes': 15})
        progress = (body.get('data') or {}).get('progress') or {}
        check('标记任务点完成', status == 200 and progress.get('status') == 'done',
              f"{target['title'][:14]} → {progress.get('status')}")
        done_after = ((body.get('data') or {}).get('stats') or {}).get('task_done')
        check('完成后统计同步', isinstance(done_after, int) and done_after >= 1, f'已完成 {done_after} 个')
    else:
        skip('标记任务点完成', '课程没有任务点章节')

    section('3. 随堂测验')
    status, body = call('GET', f'/lms/courses/{course_id}/quizzes')
    quizzes = (body.get('data') or {}).get('items') or []
    check('测验列表可读', status == 200 and len(quizzes) >= 1, f'{len(quizzes)} 套')
    if quizzes:
        quiz = quizzes[0]
        answers = [0] * quiz['count']
        status, body = call('POST', f"/lms/quizzes/{quiz['id']}/submit", {'answers': answers})
        data = body.get('data') or {}
        attempt = data.get('attempt') or {}
        check('测验可提交并自动评分', status == 200 and attempt.get('total') == quiz['count'],
              f"得分 {attempt.get('score')}／正确 {attempt.get('correct')}/{attempt.get('total')}")
        check('错题返回解析', isinstance(data.get('wrong'), list), f"错 {len(data.get('wrong') or [])} 题")

    section('4. 讨论区与 AI 助教')
    status, body = call('GET', f'/lms/courses/{course_id}/discussions')
    posts = (body.get('data') or {}).get('items') or []
    check('讨论区可读', status == 200, f'{len(posts)} 帖')
    status, body = call('POST', f'/lms/courses/{course_id}/discussions',
                        {'title': '自检提问：循环队列的队满条件为什么不能写成 rear==front？',
                         'content': '如果只用 rear==front 判断队满，队空和队满会撞在一起，那该怎么区分？'})
    post = (body.get('data') or {}).get('post') or {}
    check('学生可发帖', status == 200 and bool(post.get('id')), f"帖子 id={post.get('id')}")
    if post.get('id'):
        if fast:
            skip('AI 助教自动回帖', '--fast 模式跳过生成类检查')
        else:
            reply = None
            for _ in range(40):
                time.sleep(3)
                status, body = call('GET', f"/lms/posts/{post['id']}")
                replies = (body.get('data') or {}).get('replies') or []
                reply = next((r for r in replies if r.get('is_ai')), None)
                if reply:
                    break
            check('AI 助教自动回帖', bool(reply),
                  f"{reply.get('ai_model')} · {len(reply.get('content') or '')} 字" if reply else '120 秒内未回帖')
            if reply:
                check('AI 回帖带教材引用', len(reply.get('citations') or []) >= 1,
                      f"{len(reply.get('citations') or [])} 条引用")

    section('5. 教师视角：班级统计 / 签到 / 通知')
    status, body = call('GET', '/lms/courses')
    status, token = login('teacher@smartedu.cn', 'Teacher@123', 'teacher')
    check('教师登录', status == 200 and bool(token))
    status, body = call('GET', f'/lms/courses/{course_id}/dashboard')
    data = body.get('data') or {}
    klass = data.get('class') or {}
    check('班级统计可读', status == 200 and isinstance(klass.get('student_count'), int),
          f"{klass.get('student_count')} 人 / 平均完成率 {klass.get('avg_task_rate')}")
    check('预警名单可读', isinstance(klass.get('at_risk'), list), f"{klass.get('at_risk_count')} 人预警")

    status, body = call('POST', f'/lms/courses/{course_id}/signin', {'title': '自检签到'})
    signin = body.get('data') or {}
    check('教师可发起签到', status == 200 and bool(signin.get('code')), f"签到码 {signin.get('code')}")

    status, body = call('POST', f'/lms/courses/{course_id}/notices',
                        {'title': '自检通知', 'content': '这是一条由自检脚本发布的测试通知。'})
    notice = body.get('data') or {}
    check('教师可发通知', status == 200 and bool(notice.get('id')), f"通知 id={notice.get('id')}")

    if signin.get('id'):
        status, token = login('student2023010101@smartedu.cn', 'Student@123', 'student')
        status, body = call('POST', f"/lms/signins/{signin['id']}/checkin", {'code': '0000'})
        check('错误签到码被拒绝', status == 400, f'status={status}')
        status, body = call('POST', f"/lms/signins/{signin['id']}/checkin", {'code': signin['code']})
        check('正确签到码可签到', status == 200, (body.get('data') or {}).get('checked', ''))
    if notice.get('id'):
        status, body = call('GET', f'/lms/courses/{course_id}/notices')
        unread = (body.get('data') or {}).get('unread')
        check('学生端有未读通知', isinstance(unread, int) and unread >= 1, f'未读 {unread} 条')
        status, body = call('POST', f"/lms/notices/{notice['id']}/read")
        check('通知可标记已读', status == 200)

    section('6. AI 能力（本机模型）')
    if fast:
        for name in ('AI 章节导读', 'AI 随堂出题', 'AI 学情建议'):
            skip(name, '--fast 模式跳过生成类检查')
    else:
        status, token = login('teacher@smartedu.cn', 'Teacher@123', 'teacher')
        status, body = call('POST', f"/lms/chapters/{target['id']}/intro", {'refresh': True}) if target else (0, {})
        data = body.get('data') or {}
        check('AI 章节导读', status == 200 and len(data.get('content') or '') > 80,
              f"{(data.get('meta') or {}).get('model')} · {len(data.get('content') or '')} 字")
        check('导读带引用', len((data.get('meta') or {}).get('citations') or []) >= 1,
              f"{len((data.get('meta') or {}).get('citations') or [])} 条")

        status, body = call('POST', f"/lms/chapters/{target['id']}/quiz", {'count': 5}) if target else (0, {})
        data = body.get('data') or {}
        check('AI 随堂出题', status == 200 and (data.get('count') or 0) >= 3,
              f"{data.get('count')} 题 · {(data.get('ai_meta') or {}).get('model')}")

        status, body = call('POST', f'/lms/courses/{course_id}/advice', {})
        data = body.get('data') or {}
        check('AI 学情建议', status == 200 and len(data.get('content') or '') > 30,
              f"{(data.get('meta') or {}).get('model')} · {len(data.get('content') or '')} 字")

    print('\n' + '=' * 60)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项，跳过 {len(SKIPPED)} 项')
    if FAILED:
        for item in FAILED:
            print(f'  ✗ {item}')
        return 1
    if SKIPPED:
        print('  （跳过的检查未计入通过，避免把"没测"写成"通过"）')
    print('课程空间自检全部通过 ✅')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())