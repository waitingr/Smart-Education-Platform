"""学习通式教学闭环接口：课程空间 / 章节任务点 / 随堂测验 / 讨论区 / 签到 / 通知。

与平台其余部分一致的三条底线：
1. 数据真实：所有统计都从库里算，不编数字；
2. AI 留痕：导读 / 出题 / 助教回帖 / 学情建议都写 provider、model、耗时，
   界面上明确标注"AI 生成"，学生可核对引用；
3. 本地优先：AI 调用走 get_llm()，没有模型时退化为"知识库原文 + 规则评分"，
   不假装生成。
"""
import json
import os
import re
import threading
import time
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from auth_utils import get_current_user_from_request
from extensions import db
from kb_service import get_kb
from llm_client import get_llm
from lms_models import (ChapterProgress, Course, CourseChapter, CourseMember,
                        DiscussionPost, DiscussionReply, Notice, NoticeRead, Quiz,
                        QuizAttempt, Signin, SigninRecord)
from models import User
from text_utils import clip

lms_bp = Blueprint('lms', __name__)

ROLE_TEACHER = 'teacher'


# ==================== 通用返回 ====================
def ok(data=None, message='success'):
    return jsonify({'code': 200, 'message': message, 'data': data}), 200


def fail(message, code=400):
    return jsonify({'code': code, 'message': message, 'data': None}), code


def current_user():
    return get_current_user_from_request()


def require_user():
    user = current_user()
    if not user:
        return None, fail('请先登录后再使用课程空间', 401)
    return user, None


def is_teacher(user):
    return bool(user) and (user.role or '') == ROLE_TEACHER


def member_of(course_id, user_id):
    return CourseMember.query.filter_by(course_id=course_id, user_id=user_id).first()


def course_scope(course_id, user):
    """返回 (course, 是否是本课教师)；无权限时返回 (None, False)。"""
    course = db.session.get(Course, course_id)
    if not course:
        return None, False
    member = member_of(course.id, user.id)
    if not member and not is_teacher(user):
        return None, False
    return course, is_teacher(user)


# ==================== 统计口径 ====================
def chapter_progress_map(course_id, user_id):
    rows = (ChapterProgress.query.join(CourseChapter, ChapterProgress.chapter_id == CourseChapter.id)
            .filter(CourseChapter.course_id == course_id, ChapterProgress.user_id == user_id).all())
    return {row.chapter_id: {'status': row.status or 'todo', 'minutes': row.minutes or 0} for row in rows}


def course_stats(course_id, user_id):
    chapters = CourseChapter.query.filter_by(course_id=course_id).order_by(CourseChapter.order_index).all()
    task_points = [c for c in chapters if c.is_task_point]
    progress = chapter_progress_map(course_id, user_id)
    done = len([c for c in task_points if progress.get(c.id, {}).get('status') == 'done'])
    minutes = sum(item.get('minutes', 0) for item in progress.values())

    attempts = (QuizAttempt.query.join(Quiz, QuizAttempt.quiz_id == Quiz.id)
                .filter(Quiz.course_id == course_id, QuizAttempt.user_id == user_id).all())
    quiz_total = len(attempts)
    quiz_avg = round(sum(a.rate() for a in attempts) / quiz_total, 3) if quiz_total else None

    signins = Signin.query.filter_by(course_id=course_id).all()
    checked = 0
    for signin in signins:
        if SigninRecord.query.filter_by(signin_id=signin.id, user_id=user_id).first():
            checked += 1
    signin_total = len(signins)

    posts = DiscussionPost.query.filter_by(course_id=course_id).count()
    notices = Notice.query.filter_by(course_id=course_id).all()
    unread = 0
    for notice in notices:
        if not NoticeRead.query.filter_by(notice_id=notice.id, user_id=user_id).first():
            unread += 1

    return {
        'chapter_count': len(chapters),
        'task_total': len(task_points),
        'task_done': done,
        'task_rate': round(done / len(task_points), 3) if task_points else 0.0,
        'minutes': minutes,
        'quiz_count': quiz_total,
        'quiz_avg': quiz_avg,
        'signin_total': signin_total,
        'signin_checked': checked,
        'signin_rate': round(checked / signin_total, 3) if signin_total else None,
        'post_count': posts,
        'unread_notices': unread,
    }


def class_stats(course_id):
    """教师视角：全班平均完成率 / 测验均分 / 出勤率。"""
    members = CourseMember.query.filter_by(course_id=course_id, role='student').all()
    student_ids = [m.user_id for m in members]
    if not student_ids:
        return {'student_count': 0, 'avg_task_rate': None, 'avg_quiz_rate': None,
                'avg_signin_rate': None, 'at_risk': []}

    chapters = CourseChapter.query.filter_by(course_id=course_id, is_task_point=True).all()
    task_ids = {c.id for c in chapters}
    progress_rows = (ChapterProgress.query.join(CourseChapter, ChapterProgress.chapter_id == CourseChapter.id)
                     .filter(CourseChapter.course_id == course_id, ChapterProgress.user_id.in_(student_ids)).all())
    done_map = {}
    for row in progress_rows:
        if row.chapter_id in task_ids and row.status == 'done':
            done_map[row.user_id] = done_map.get(row.user_id, 0) + 1
    task_rates = {uid: (done_map.get(uid, 0) / len(task_ids) if task_ids else 0) for uid in student_ids}

    quiz_rows = (QuizAttempt.query.join(Quiz, QuizAttempt.quiz_id == Quiz.id)
                 .filter(Quiz.course_id == course_id, QuizAttempt.user_id.in_(student_ids)).all())
    quiz_map = {}
    for row in quiz_rows:
        quiz_map.setdefault(row.user_id, []).append(row.rate())
    quiz_rates = {uid: (sum(v) / len(v) if v else None) for uid, v in quiz_map.items()}

    signins = Signin.query.filter_by(course_id=course_id).all()
    checked_map = {}
    if signins:
        records = SigninRecord.query.filter(SigninRecord.signin_id.in_([s.id for s in signins]),
                                            SigninRecord.user_id.in_(student_ids)).all()
        for record in records:
            checked_map[record.user_id] = checked_map.get(record.user_id, 0) + 1
    signin_rates = {uid: (checked_map.get(uid, 0) / len(signins) if signins else None) for uid in student_ids}

    usernames = {u.id: u.username for u in User.query.filter(User.id.in_(student_ids)).all()}
    at_risk = []
    for uid in student_ids:
        task_rate = task_rates.get(uid, 0)
        quiz_rate = quiz_rates.get(uid)
        signin_rate = signin_rates.get(uid)
        reasons = []
        if task_rate < 0.6:
            reasons.append(f'任务点完成 {int(task_rate * 100)}%')
        if quiz_rate is not None and quiz_rate < 0.6:
            reasons.append(f'测验正确率 {int(quiz_rate * 100)}%')
        if signin_rate is not None and signin_rate < 0.8:
            reasons.append(f'出勤 {int(signin_rate * 100)}%')
        if reasons:
            at_risk.append({'user_id': uid, 'name': usernames.get(uid, f'学生{uid}'), 'reasons': reasons})

    def _avg(values):
        usable = [v for v in values if v is not None]
        return round(sum(usable) / len(usable), 3) if usable else None

    return {
        'student_count': len(student_ids),
        'avg_task_rate': _avg(list(task_rates.values())),
        'avg_quiz_rate': _avg(list(quiz_rates.values())),
        'avg_signin_rate': _avg(list(signin_rates.values())),
        'at_risk': sorted(at_risk, key=lambda item: -len(item['reasons']))[:8],
        'at_risk_count': len(at_risk),
    }


# ==================== AI 能力 ====================
def _chunks(query, top_k=4):
    try:
        return get_kb().search(query, top_k=top_k) or []
    except Exception:
        return []


def _context(chunks, max_chars=None):
    if max_chars is None:
        max_chars = 2400
        try:
            from config import Config
            if get_llm().provider in ('ollama', 'local'):
                max_chars = max(1200, Config.LLM_CONTEXT_CHARS // 2)
        except Exception:
            pass
    blocks, used = [], 0
    for chunk in chunks:
        text = chunk.get('text') or chunk.get('snippet') or ''
        if used + len(text) > max_chars:
            text = clip(text, max(0, max_chars - used))
        if not text:
            break
        blocks.append(f"[{chunk.get('index')}] 来源：{chunk.get('citation') or chunk.get('locator')}\n{text}")
        used += len(text)
        if used >= max_chars:
            break
    return '\n\n'.join(blocks)


def _citations(chunks, limit=3):
    items = []
    for chunk in chunks[:limit]:
        items.append({
            'index': chunk.get('index'),
            'locator': chunk.get('locator') or '',
            'doc_title': chunk.get('doc_title') or '',
            'source_type': chunk.get('source_type') or '',
            'citation': chunk.get('citation') or chunk.get('locator') or '',
        })
    return items


def _chat(system, user, chunks=None, task='lms'):
    """统一的生成调用：把 LLMResult 规整成普通 dict，便于上层取值。"""
    result = get_llm().complete(
        system=system,
        user=user,
        question=user[:200],
        chunks=chunks or [],
        task=task,
        style={'level': 'intermediate', 'detail': 'standard', 'style': 'balanced'},
        temperature=0.25,
    )
    return {
        'content': getattr(result, 'text', '') or '',
        'provider': getattr(result, 'provider', 'offline'),
        'model': getattr(result, 'model', 'retrieval-composer'),
        'degraded': bool(getattr(result, 'degraded', True)),
        'latency_ms': int(getattr(result, 'latency_ms', 0) or 0),
    }


def _extract_json_array(text):
    """从模型输出里抠出第一个 JSON 数组（兼容 ```json 包裹与前后废话）。"""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r'^```(?:json)?|```$', '', cleaned, flags=re.MULTILINE).strip()
    start = cleaned.find('[')
    end = cleaned.rfind(']')
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except ValueError:
        return None


QUIZ_SYSTEM = (
    '你是《数据结构与算法》课程的命题助教。只输出 JSON 数组，不要任何解释文字。\n'
    '数组每个元素形如：{"stem":"题干","options":["A选项","B选项","C选项","D选项"],'
    '"answer":0,"explanation":"为什么选它，一句话"}。\n'
    'answer 是正确选项下标（0-3）。题目必须能在给定资料里找到依据，不要超纲。'
)


def _ai_intro(chapter, course):
    query = f"{chapter.title} {' '.join(_loads_list(chapter.key_points))}"
    chunks = _chunks(query, top_k=4)
    prompt = (
        f"【章节】{course.title} · {chapter.title}\n"
        f"【章节简介】{chapter.summary or '（无）'}\n"
        f"【检索资料】\n{_context(chunks) or '（知识库未命中，请基于课程常识谨慎回答并说明）'}\n\n"
        '请写一段 3 分钟能读完的课前导读，结构固定为：\n'
        '## 这一章解决什么问题\n## 三个必会要点（每条一句话 + 标注 [编号]）\n'
        '## 一个常见误区\n## 自测一问\n'
        '要求：面向大二学生，语言口语化，所有结论标注依据编号。'
    )
    started = time.time()
    result = _chat(
        '你是《数据结构与算法》课程的资深助教，负责写课前导读。'
        '只用给定资料，资料不足时明确说"知识库未覆盖"，不要编造教材内容。',
        prompt, chunks, task='lms_intro')
    text = (result.get('content') or '').strip()
    if not text:
        return None, {'provider': result.get('provider'), 'model': result.get('model'),
                      'error': result.get('error') or '模型未返回内容'}
    meta = {
        'provider': result.get('provider'),
        'model': result.get('model'),
        'degraded': bool(result.get('degraded')),
        'latency_ms': int((time.time() - started) * 1000),
        'citations': _citations(chunks),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    return text, meta


def _loads_list(text):
    try:
        value = json.loads(text or '[]')
        return value if isinstance(value, list) else []
    except ValueError:
        return []


def _ai_quiz(chapter, course, count=5):
    chunks = _chunks(f"{chapter.title} {chapter.summary or ''}", top_k=4)
    prompt = (
        f"【章节】{course.title} · {chapter.title}\n"
        f"【知识点】{', '.join(_loads_list(chapter.key_points)) or chapter.summary or '见资料'}\n"
        f"【资料】\n{_context(chunks)}\n\n"
        f'请出 {count} 道单选题，覆盖不同知识点，难度为期末考水平。'
    )
    started = time.time()
    result = _chat(QUIZ_SYSTEM, prompt, chunks, task='lms_quiz')
    questions = _extract_json_array(result.get('content') or '')
    valid = []
    for item in questions or []:
        if not isinstance(item, dict):
            continue
        options = item.get('options') or []
        answer = item.get('answer')
        stem = (item.get('stem') or '').strip()
        if not stem or len(options) != 4 or not isinstance(answer, int) or not 0 <= answer <= 3:
            continue
        valid.append({
            'stem': stem,
            'options': [str(option) for option in options],
            'answer': answer,
            'explanation': (item.get('explanation') or '').strip(),
        })
    meta = {
        'provider': result.get('provider'),
        'model': result.get('model'),
        'degraded': bool(result.get('degraded')),
        'latency_ms': int((time.time() - started) * 1000),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    return valid, meta


def _ai_tutor_reply(post_id, app):
    """后台线程：为学生提问生成带引用的助教回答。"""
    with app.app_context():
        try:
            post = db.session.get(DiscussionPost, post_id)
            if not post:
                return
            question = f"{post.title}\n{post.content}"
            chunks = _chunks(question, top_k=4)
            result = _chat(
                '你是课程讨论区的 AI 助教。回答要短（不超过 200 字）：先给结论，再点出常见误区，'
                '结论后面用 [编号] 标注依据。只要检索资料能支撑结论，就不要再写"知识库未覆盖"这类话；'
                '只有在确实没有相关资料时才说明，并建议问老师。',
                f'学生提问：\n{question}\n\n【检索资料】\n{_context(chunks)}',
                chunks, task='lms_discuss')
            text = (result.get('content') or '').strip()
            if not text:
                text = '（AI 助教暂时不可用：本机模型未返回内容，请稍后重试或直接问老师。）'
            reply = DiscussionReply(
                post_id=post.id,
                user_id=None,
                is_ai=True,
                ai_model=result.get('model') or 'offline',
                content=text,
                citations=json.dumps(_citations(chunks), ensure_ascii=False),
            )
            db.session.add(reply)
            post.reply_count = (post.reply_count or 0) + 1
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            print(f'[lms] AI 助教回帖失败：{error}')


# ==================== 课程 ====================
@lms_bp.route('/courses', methods=['GET'])
def list_courses():
    user, error = require_user()
    if error:
        return error
    if is_teacher(user):
        courses = (Course.query.join(CourseMember, CourseMember.course_id == Course.id)
                   .filter(CourseMember.user_id == user.id).order_by(Course.id).all())
    else:
        courses = (Course.query.join(CourseMember, CourseMember.course_id == Course.id)
                   .filter(CourseMember.user_id == user.id).order_by(Course.id).all())
    teacher_names = {u.id: u.username for u in User.query.filter(User.id.in_([c.teacher_id for c in courses if c.teacher_id])).all()}
    items = []
    for course in courses:
        stats = course_stats(course.id, user.id)
        item = course.to_dict(stats)
        item['teacher_name'] = teacher_names.get(course.teacher_id, '')
        item['student_count'] = CourseMember.query.filter_by(course_id=course.id, role='student').count()
        items.append(item)
    return ok({'items': items, 'is_teacher': is_teacher(user)})


@lms_bp.route('/courses', methods=['POST'])
def create_course():
    user, error = require_user()
    if error:
        return error
    if not is_teacher(user):
        return fail('只有教师可以创建课程', 403)
    payload = request.get_json(silent=True) or {}
    title = (payload.get('title') or '').strip()
    if not title:
        return fail('课程名称不能为空')
    code = (payload.get('code') or '').strip() or f'{int(time.time()) % 100000:05d}'
    if Course.query.filter_by(code=code).first():
        return fail('课程邀请码已被占用，请换一个')
    course = Course(title=title, code=code, term=(payload.get('term') or '').strip() or '2026 秋',
                    description=(payload.get('description') or '').strip(), teacher_id=user.id)
    db.session.add(course)
    db.session.flush()
    db.session.add(CourseMember(course_id=course.id, user_id=user.id, role=ROLE_TEACHER))
    db.session.commit()
    return ok(course.to_dict(), '课程已创建')


@lms_bp.route('/courses/join', methods=['POST'])
def join_course():
    user, error = require_user()
    if error:
        return error
    code = ((request.get_json(silent=True) or {}).get('code') or '').strip()
    course = Course.query.filter_by(code=code).first()
    if not course:
        return fail('邀请码不存在，请向老师确认', 404)
    if member_of(course.id, user.id):
        return ok(course.to_dict(), '你已在课程中')
    db.session.add(CourseMember(course_id=course.id, user_id=user.id, role='student'))
    db.session.commit()
    return ok(course.to_dict(), '已加入课程')


@lms_bp.route('/courses/<int:course_id>', methods=['GET'])
def course_detail(course_id):
    user, error = require_user()
    if error:
        return error
    course, teacher_view = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    chapters = CourseChapter.query.filter_by(course_id=course.id).order_by(CourseChapter.order_index).all()
    progress = chapter_progress_map(course.id, user.id)
    data = {
        'course': course.to_dict(course_stats(course.id, user.id)),
        'chapters': [chapter.to_dict(progress.get(chapter.id)) for chapter in chapters],
        'teacher_view': teacher_view,
        'teacher_name': (db.session.get(User, course.teacher_id).username if course.teacher_id else ''),
        'class': class_stats(course.id) if teacher_view else None,
    }
    return ok(data)


# ==================== 章节任务点 ====================
@lms_bp.route('/chapters/<int:chapter_id>/intro', methods=['POST'])
def chapter_intro(chapter_id):
    user, error = require_user()
    if error:
        return error
    chapter = db.session.get(CourseChapter, chapter_id)
    if not chapter:
        return fail('章节不存在', 404)
    course, teacher_view = course_scope(chapter.course_id, user)
    if not course:
        return fail('没有权限', 403)
    payload = request.get_json(silent=True) or {}
    if chapter.intro_cache and not payload.get('refresh'):
        meta = {}
        try:
            meta = json.loads(chapter.intro_meta or '{}')
        except ValueError:
            meta = {}
        return ok({'content': chapter.intro_cache, 'meta': meta, 'cached': True})
    text, meta = _ai_intro(chapter, course)
    if not text:
        return fail(f"AI 导读生成失败：{meta.get('error') or '模型未返回内容'}", 502)
    chapter.intro_cache = text
    chapter.intro_meta = json.dumps(meta, ensure_ascii=False)
    db.session.commit()
    return ok({'content': text, 'meta': meta, 'cached': False})


@lms_bp.route('/chapters/<int:chapter_id>/progress', methods=['POST'])
def update_progress(chapter_id):
    user, error = require_user()
    if error:
        return error
    chapter = db.session.get(CourseChapter, chapter_id)
    if not chapter:
        return fail('章节不存在', 404)
    course, _ = course_scope(chapter.course_id, user)
    if not course:
        return fail('没有权限', 403)
    payload = request.get_json(silent=True) or {}
    status = payload.get('status') or 'done'
    if status not in ('todo', 'doing', 'done'):
        return fail('状态只能是 todo / doing / done')
    record = ChapterProgress.query.filter_by(chapter_id=chapter.id, user_id=user.id).first()
    if not record:
        record = ChapterProgress(chapter_id=chapter.id, user_id=user.id)
        db.session.add(record)
    record.status = status
    minutes = payload.get('minutes')
    if isinstance(minutes, int) and minutes >= 0:
        record.minutes = minutes
    elif status == 'done' and not record.minutes:
        record.minutes = chapter.est_minutes or 0
    db.session.commit()
    stats = course_stats(course.id, user.id)
    return ok({'progress': {'status': record.status, 'minutes': record.minutes or 0}, 'stats': stats},
              '进度已更新')


# ==================== 随堂测验 ====================
@lms_bp.route('/courses/<int:course_id>/quizzes', methods=['GET'])
def list_quizzes(course_id):
    user, error = require_user()
    if error:
        return error
    course, teacher_view = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    quizzes = Quiz.query.filter_by(course_id=course.id).order_by(Quiz.id.desc()).all()
    items = []
    for quiz in quizzes:
        attempt = (QuizAttempt.query.filter_by(quiz_id=quiz.id, user_id=user.id)
                   .order_by(QuizAttempt.id.desc()).first())
        items.append(quiz.to_dict(with_answers=teacher_view, user_attempt=attempt.to_dict() if attempt else None))
    return ok({'items': items, 'teacher_view': teacher_view})


@lms_bp.route('/chapters/<int:chapter_id>/quiz', methods=['POST'])
def generate_quiz(chapter_id):
    user, error = require_user()
    if error:
        return error
    chapter = db.session.get(CourseChapter, chapter_id)
    if not chapter:
        return fail('章节不存在', 404)
    course, teacher_view = course_scope(chapter.course_id, user)
    if not course:
        return fail('没有权限', 403)
    payload = request.get_json(silent=True) or {}
    count = payload.get('count') or 5
    try:
        count = max(3, min(8, int(count)))
    except (TypeError, ValueError):
        count = 5
    questions, meta = _ai_quiz(chapter, course, count=count)
    if not questions:
        return fail('AI 出题失败：模型没有返回可解析的题目，请重试', 502)
    quiz = Quiz(course_id=course.id, chapter_id=chapter.id,
                title=f'{chapter.title} · 随堂测验（{len(questions)} 题）',
                questions=json.dumps(questions, ensure_ascii=False),
                created_by=user.id, ai_meta=json.dumps(meta, ensure_ascii=False))
    db.session.add(quiz)
    db.session.commit()
    return ok(quiz.to_dict(with_answers=True), '试卷已生成')


@lms_bp.route('/quizzes/<int:quiz_id>', methods=['GET'])
def quiz_detail(quiz_id):
    user, error = require_user()
    if error:
        return error
    quiz = db.session.get(Quiz, quiz_id)
    if not quiz:
        return fail('测验不存在', 404)
    course, teacher_view = course_scope(quiz.course_id, user)
    if not course:
        return fail('没有权限', 403)
    attempt = QuizAttempt.query.filter_by(quiz_id=quiz.id, user_id=user.id).order_by(QuizAttempt.id.desc()).first()
    return ok(quiz.to_dict(with_answers=teacher_view, user_attempt=attempt.to_dict() if attempt else None))


@lms_bp.route('/quizzes/<int:quiz_id>/submit', methods=['POST'])
def submit_quiz(quiz_id):
    user, error = require_user()
    if error:
        return error
    quiz = db.session.get(Quiz, quiz_id)
    if not quiz:
        return fail('测验不存在', 404)
    course, _ = course_scope(quiz.course_id, user)
    if not course:
        return fail('没有权限', 403)
    payload = request.get_json(silent=True) or {}
    answers = payload.get('answers') or []
    questions = json.loads(quiz.questions or '[]')
    if not isinstance(answers, list) or len(answers) != len(questions):
        return fail(f'需要提交 {len(questions)} 个答案，当前收到 {len(answers) if isinstance(answers, list) else 0} 个')
    detail, correct = [], 0
    for index, question in enumerate(questions):
        chosen = answers[index]
        right = question.get('answer', -1)
        hit = chosen == right
        correct += 1 if hit else 0
        detail.append({
            'index': index,
            'chosen': chosen,
            'answer': right,
            'correct': hit,
            'explanation': question.get('explanation', ''),
        })
    total = len(questions)
    score = round(correct / total * 100) if total else 0
    attempt = QuizAttempt(quiz_id=quiz.id, user_id=user.id, answers=json.dumps(answers),
                          score=score, total=total, correct=correct,
                          detail=json.dumps(detail, ensure_ascii=False))
    db.session.add(attempt)
    db.session.commit()
    wrong = [item for item in detail if not item['correct']]
    return ok({
        'attempt': attempt.to_dict(),
        'wrong': wrong,
        'advice': ('全部正确，可以挑战下一个章节的任务点了。' if not wrong
                   else f'错 {len(wrong)} 题，建议回看对应知识点后到「错题归因」里再做一次同类题。'),
    }, '已提交')


# ==================== 讨论区 ====================
@lms_bp.route('/courses/<int:course_id>/discussions', methods=['GET'])
def list_discussions(course_id):
    user, error = require_user()
    if error:
        return error
    course, _ = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    posts = (DiscussionPost.query.filter_by(course_id=course.id)
             .order_by(DiscussionPost.pinned.desc(), DiscussionPost.id.desc()).limit(30).all())
    names = {u.id: u.username for u in User.query.filter(User.id.in_([p.user_id for p in posts if p.user_id])).all()}
    items = []
    for post in posts:
        replies = DiscussionReply.query.filter_by(post_id=post.id).order_by(DiscussionReply.id).all()
        data = post.to_dict([reply.to_dict() for reply in replies])
        data['author'] = names.get(post.user_id, '匿名')
        items.append(data)
    return ok({'items': items})


@lms_bp.route('/courses/<int:course_id>/discussions', methods=['POST'])
def create_discussion(course_id):
    user, error = require_user()
    if error:
        return error
    course, _ = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    payload = request.get_json(silent=True) or {}
    title = (payload.get('title') or '').strip()
    content = (payload.get('content') or '').strip()
    if not title:
        return fail('标题不能为空')
    post = DiscussionPost(course_id=course.id, chapter_id=payload.get('chapter_id'),
                          user_id=user.id, title=title, content=content, reply_count=0)
    db.session.add(post)
    db.session.commit()
    want_ai = payload.get('ai_reply', True)
    if want_ai:
        app = current_app._get_current_object()
        threading.Thread(target=_ai_tutor_reply, args=(post.id, app), daemon=True).start()
    return ok({'post': post.to_dict(), 'ai_pending': bool(want_ai)}, '已发布，AI 助教正在查看')


@lms_bp.route('/posts/<int:post_id>/reply', methods=['POST'])
def reply_discussion(post_id):
    user, error = require_user()
    if error:
        return error
    post = db.session.get(DiscussionPost, post_id)
    if not post:
        return fail('帖子不存在', 404)
    course, _ = course_scope(post.course_id, user)
    if not course:
        return fail('没有权限', 403)
    content = ((request.get_json(silent=True) or {}).get('content') or '').strip()
    if not content:
        return fail('回复内容不能为空')
    reply = DiscussionReply(post_id=post.id, user_id=user.id, is_ai=False, content=content)
    db.session.add(reply)
    post.reply_count = (post.reply_count or 0) + 1
    db.session.commit()
    return ok(reply.to_dict(), '回复成功')


@lms_bp.route('/posts/<int:post_id>', methods=['GET'])
def post_detail(post_id):
    user, error = require_user()
    if error:
        return error
    post = db.session.get(DiscussionPost, post_id)
    if not post:
        return fail('帖子不存在', 404)
    course, _ = course_scope(post.course_id, user)
    if not course:
        return fail('没有权限', 403)
    replies = DiscussionReply.query.filter_by(post_id=post.id).order_by(DiscussionReply.id).all()
    names = {u.id: u.username for u in User.query.filter(User.id.in_([r.user_id for r in replies if r.user_id])).all()}
    data = post.to_dict([])
    data['author'] = (db.session.get(User, post.user_id).username if post.user_id else '匿名')
    data['replies'] = [dict(reply.to_dict(), author=names.get(reply.user_id, 'AI 助教' if reply.is_ai else '匿名'))
                       for reply in replies]
    data['ai_pending'] = any(reply.is_ai for reply in replies) is False and (post.reply_count or 0) == 0
    return ok(data)


# ==================== 签到 ====================
@lms_bp.route('/courses/<int:course_id>/signins', methods=['GET'])
def list_signins(course_id):
    user, error = require_user()
    if error:
        return error
    course, teacher_view = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    signins = Signin.query.filter_by(course_id=course.id).order_by(Signin.id.desc()).limit(10).all()
    total = CourseMember.query.filter_by(course_id=course.id, role='student').count()
    items = []
    for signin in signins:
        checked = SigninRecord.query.filter_by(signin_id=signin.id).count()
        item = signin.to_dict(checked=checked, total=total)
        item['mine'] = bool(SigninRecord.query.filter_by(signin_id=signin.id, user_id=user.id).first())
        items.append(item)
    return ok({'items': items, 'teacher_view': teacher_view})


@lms_bp.route('/courses/<int:course_id>/signin', methods=['POST'])
def create_signin(course_id):
    user, error = require_user()
    if error:
        return error
    if not is_teacher(user):
        return fail('只有教师可以发起签到', 403)
    course, _ = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    payload = request.get_json(silent=True) or {}
    import random
    code = f'{random.randint(1000, 9999)}'
    signin = Signin(course_id=course.id, code=code,
                    title=(payload.get('title') or '课堂签到').strip(),
                    created_by=user.id, expire_minutes=int(payload.get('expire_minutes') or 10))
    db.session.add(signin)
    db.session.commit()
    return ok(signin.to_dict(checked=0, total=CourseMember.query.filter_by(course_id=course.id, role='student').count()),
              '签到已发起')


@lms_bp.route('/signins/<int:signin_id>/checkin', methods=['POST'])
def checkin(signin_id):
    user, error = require_user()
    if error:
        return error
    signin = db.session.get(Signin, signin_id)
    if not signin:
        return fail('签到不存在', 404)
    course, _ = course_scope(signin.course_id, user)
    if not course:
        return fail('没有权限', 403)
    if signin.closed:
        return fail('签到已结束')
    if signin.expired():
        return fail('签到已超时，请让老师重新发起')
    code = ((request.get_json(silent=True) or {}).get('code') or '').strip()
    if code != signin.code:
        return fail('签到码不对，请核对黑板上的 4 位数字')
    if SigninRecord.query.filter_by(signin_id=signin.id, user_id=user.id).first():
        return ok({'already': True}, '你已经签过了')
    db.session.add(SigninRecord(signin_id=signin.id, user_id=user.id))
    db.session.commit()
    return ok({'already': False, 'checked': SigninRecord.query.filter_by(signin_id=signin.id).count()}, '签到成功')


@lms_bp.route('/signins/<int:signin_id>/close', methods=['POST'])
def close_signin(signin_id):
    user, error = require_user()
    if error:
        return error
    if not is_teacher(user):
        return fail('只有教师可以结束签到', 403)
    signin = db.session.get(Signin, signin_id)
    if not signin:
        return fail('签到不存在', 404)
    signin.closed = True
    db.session.commit()
    return ok(signin.to_dict(), '签到已结束')


# ==================== 通知 ====================
@lms_bp.route('/courses/<int:course_id>/notices', methods=['GET'])
def list_notices(course_id):
    user, error = require_user()
    if error:
        return error
    course, teacher_view = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    notices = (Notice.query.filter_by(course_id=course.id)
               .order_by(Notice.pinned.desc(), Notice.id.desc()).limit(20).all())
    read_ids = {row.notice_id for row in NoticeRead.query.filter_by(user_id=user.id).all()}
    return ok({'items': [notice.to_dict(read=notice.id in read_ids) for notice in notices],
               'teacher_view': teacher_view,
               'unread': len([n for n in notices if n.id not in read_ids])})


@lms_bp.route('/courses/<int:course_id>/notices', methods=['POST'])
def create_notice(course_id):
    user, error = require_user()
    if error:
        return error
    if not is_teacher(user):
        return fail('只有教师可以发布通知', 403)
    course, _ = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    payload = request.get_json(silent=True) or {}
    title = (payload.get('title') or '').strip()
    if not title:
        return fail('通知标题不能为空')
    notice = Notice(course_id=course.id, title=title,
                    content=(payload.get('content') or '').strip(),
                    created_by=user.id, pinned=bool(payload.get('pinned')))
    db.session.add(notice)
    db.session.commit()
    return ok(notice.to_dict(), '通知已发布')


@lms_bp.route('/notices/<int:notice_id>/read', methods=['POST'])
def read_notice(notice_id):
    user, error = require_user()
    if error:
        return error
    notice = db.session.get(Notice, notice_id)
    if not notice:
        return fail('通知不存在', 404)
    if not member_of(notice.course_id, user.id) and not is_teacher(user):
        return fail('没有权限', 403)
    if not NoticeRead.query.filter_by(notice_id=notice.id, user_id=user.id).first():
        db.session.add(NoticeRead(notice_id=notice.id, user_id=user.id))
        db.session.commit()
    return ok({'read': True})


# ==================== 学习看板与 AI 学情建议 ====================
@lms_bp.route('/courses/<int:course_id>/dashboard', methods=['GET'])
def dashboard(course_id):
    user, error = require_user()
    if error:
        return error
    course, teacher_view = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    mine = course_stats(course.id, user.id)
    data = {'course': course.to_dict(), 'mine': mine, 'teacher_view': teacher_view,
            'class': class_stats(course.id) if teacher_view else None}
    return ok(data)


@lms_bp.route('/courses/<int:course_id>/advice', methods=['POST'])
def learning_advice(course_id):
    """把真实统计喂给本机模型，生成班级/个人学习建议（不编数据，只解读给定统计）。"""
    user, error = require_user()
    if error:
        return error
    course, teacher_view = course_scope(course_id, user)
    if not course:
        return fail('课程不存在或你没有权限', 404)
    mine = course_stats(course.id, user.id)
    klass = class_stats(course.id) if teacher_view else None

    if teacher_view and klass:
        facts = (
            f"课程：{course.title}\n班级人数：{klass['student_count']}\n"
            f"任务点平均完成率：{klass['avg_task_rate']}\n"
            f"测验平均正确率：{klass['avg_quiz_rate']}\n"
            f"平均出勤率：{klass['avg_signin_rate']}\n"
            f"预警人数：{klass['at_risk_count']}\n"
            f"预警明细：{json.dumps(klass['at_risk'], ensure_ascii=False)[:600]}"
        )
        role_hint = '以任课教师为读者，给出 3 条可直接执行的班级干预动作'
    else:
        facts = (
            f"课程：{course.title}\n任务点完成：{mine['task_done']}/{mine['task_total']}（完成率 {mine['task_rate']}）\n"
            f"测验次数：{mine['quiz_count']}，平均正确率：{mine['quiz_avg']}\n"
            f"签到：{mine['signin_checked']}/{mine['signin_total']}，学习时长累计 {mine['minutes']} 分钟\n"
            f"讨论区发帖：{mine['post_count']}"
        )
        role_hint = '以学生本人为读者，给出 3 条今天就能做的下一步动作'

    prompt = (
        f'【课程真实统计】\n{facts}\n\n'
        f'请{role_hint}。要求：\n'
        '1) 先一句话总结当前学习状态；\n2) 再列 3 条动作，每条不超过 40 字且可当天执行；\n'
        '3) 只依据上面的数字，不要虚构数据；数字不足时直接说明。'
    )
    started = time.time()
    result = _chat('你是课程学习分析师，擅长把学习数据翻译成具体动作。', prompt, [], task='lms_advice')
    content = (result.get('content') or '').strip()
    if not content:
        return fail('AI 建议生成失败：模型未返回内容', 502)
    return ok({
        'content': content,
        'facts': {'mine': mine, 'class': klass},
        'meta': {'provider': result.get('provider'), 'model': result.get('model'),
                 'degraded': bool(result.get('degraded')),
                 'latency_ms': int((time.time() - started) * 1000)},
    })


# ==================== 我的待办（跨课程聚合，仿学习通首页） ====================
@lms_bp.route('/overview', methods=['GET'])
def overview():
    user, error = require_user()
    if error:
        return error
    memberships = CourseMember.query.filter_by(user_id=user.id).all()
    todo, unread_total = [], 0
    for membership in memberships:
        course = db.session.get(Course, membership.course_id)
        if not course:
            continue
        stats = course_stats(course.id, user.id)
        unread_total += stats['unread_notices']
        chapters = (CourseChapter.query.filter_by(course_id=course.id, is_task_point=True)
                    .order_by(CourseChapter.order_index).all())
        progress = chapter_progress_map(course.id, user.id)
        pending = [c for c in chapters if progress.get(c.id, {}).get('status') != 'done']
        if pending:
            todo.append({
                'course_id': course.id,
                'course_title': course.title,
                'type': 'task',
                'label': f'{pending[0].title}',
                'detail': f'还有 {len(pending)} 个任务点未完成',
            })
        quizzes = Quiz.query.filter_by(course_id=course.id).all()
        for quiz in quizzes:
            if not QuizAttempt.query.filter_by(quiz_id=quiz.id, user_id=user.id).first():
                todo.append({'course_id': course.id, 'course_title': course.title,
                             'type': 'quiz', 'label': quiz.title, 'detail': '测验未提交'})
    return ok({'todo': todo[:10], 'unread_notices': unread_total,
               'course_count': len(memberships)})


def _loads(text, fallback):
    try:
        return json.loads(text) if text else fallback
    except ValueError:
        return fallback