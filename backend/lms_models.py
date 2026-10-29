"""学习通式教学闭环的数据模型（课程 / 章节任务点 / 测验 / 讨论 / 签到 / 通知）。

设计原则与平台一致：
- 所有学习行为都落到数据库，统计口径可核查，不编数据；
- AI 参与的地方（导读、出题、助教回帖、学情建议）都记下 provider / model / 耗时，
  便于在界面上标明"这段是 AI 生成的"；
- 表名统一 lms_ 前缀，避免与已有的 users / homeworks 等表混在一起。
"""
import json
from datetime import datetime

from extensions import db


def _loads(text, fallback):
    try:
        value = json.loads(text) if text else fallback
        return value if value is not None else fallback
    except (ValueError, TypeError):
        return fallback


class Course(db.Model):
    __tablename__ = 'lms_courses'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)      # 加入课程用的邀请码
    term = db.Column(db.String(50), nullable=True)
    description = db.Column(db.Text, nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    color = db.Column(db.String(20), default='#2d8cf0')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self, stats=None):
        data = {
            'id': self.id,
            'title': self.title,
            'code': self.code,
            'term': self.term or '',
            'description': self.description or '',
            'teacher_id': self.teacher_id,
            'color': self.color or '#2d8cf0',
            'created_at': self.created_at.strftime('%Y-%m-%d') if self.created_at else '',
        }
        if stats:
            data.update(stats)
        return data


class CourseMember(db.Model):
    __tablename__ = 'lms_course_members'
    __table_args__ = (db.UniqueConstraint('course_id', 'user_id', name='uq_lms_member'),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(db.Integer, db.ForeignKey('lms_courses.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    role = db.Column(db.String(20), default='student')      # student / teacher
    joined_at = db.Column(db.DateTime, default=datetime.now)


class CourseChapter(db.Model):
    __tablename__ = 'lms_chapters'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(db.Integer, db.ForeignKey('lms_courses.id', ondelete='CASCADE'), nullable=False, index=True)
    order_index = db.Column(db.Integer, default=0)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    key_points = db.Column(db.Text, nullable=True)                  # JSON list
    est_minutes = db.Column(db.Integer, default=20)
    is_task_point = db.Column(db.Boolean, default=True)             # 学习通式"任务点"
    intro_cache = db.Column(db.Text, nullable=True)                 # AI 导读正文
    intro_meta = db.Column(db.Text, nullable=True)                  # JSON：provider/model/ms/citations
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self, progress=None):
        return {
            'id': self.id,
            'course_id': self.course_id,
            'order_index': self.order_index or 0,
            'title': self.title,
            'summary': self.summary or '',
            'key_points': _loads(self.key_points, []),
            'est_minutes': self.est_minutes or 0,
            'is_task_point': bool(self.is_task_point),
            'has_intro': bool(self.intro_cache),
            'progress': progress or {'status': 'todo', 'minutes': 0},
        }


class ChapterProgress(db.Model):
    __tablename__ = 'lms_chapter_progress'
    __table_args__ = (db.UniqueConstraint('chapter_id', 'user_id', name='uq_lms_progress'),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey('lms_chapters.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    status = db.Column(db.String(20), default='todo')               # todo / doing / done
    minutes = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class Quiz(db.Model):
    __tablename__ = 'lms_quizzes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(db.Integer, db.ForeignKey('lms_courses.id', ondelete='CASCADE'), nullable=False, index=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey('lms_chapters.id', ondelete='SET NULL'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    questions = db.Column(db.Text, nullable=False)                   # JSON list
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ai_meta = db.Column(db.Text, nullable=True)                      # JSON：provider/model/ms
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self, with_answers=False, user_attempt=None):
        questions = _loads(self.questions, [])
        items = []
        for question in questions:
            item = {
                'stem': question.get('stem', ''),
                'options': question.get('options', []),
                'explanation': question.get('explanation', ''),
            }
            if with_answers:
                item['answer'] = question.get('answer', -1)
            items.append(item)
        data = {
            'id': self.id,
            'course_id': self.course_id,
            'chapter_id': self.chapter_id,
            'title': self.title,
            'count': len(items),
            'questions': items,
            'ai_meta': _loads(self.ai_meta, {}),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }
        if user_attempt is not None:
            data['my_attempt'] = user_attempt
        return data


class QuizAttempt(db.Model):
    __tablename__ = 'lms_quiz_attempts'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('lms_quizzes.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    answers = db.Column(db.Text, nullable=True)                      # JSON list[int]
    score = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=0)
    correct = db.Column(db.Integer, default=0)
    detail = db.Column(db.Text, nullable=True)                       # JSON：逐题对错
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def rate(self):
        return round((self.correct or 0) / self.total, 3) if self.total else 0.0

    def to_dict(self):
        return {
            'id': self.id,
            'quiz_id': self.quiz_id,
            'score': self.score or 0,
            'total': self.total or 0,
            'correct': self.correct or 0,
            'rate': self.rate(),
            'detail': _loads(self.detail, []),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }


class DiscussionPost(db.Model):
    __tablename__ = 'lms_posts'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(db.Integer, db.ForeignKey('lms_courses.id', ondelete='CASCADE'), nullable=False, index=True)
    chapter_id = db.Column(db.Integer, db.ForeignKey('lms_chapters.id', ondelete='SET NULL'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False, default='')
    pinned = db.Column(db.Boolean, default=False)
    reply_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def to_dict(self, replies=None):
        return {
            'id': self.id,
            'course_id': self.course_id,
            'chapter_id': self.chapter_id,
            'title': self.title,
            'content': self.content,
            'pinned': bool(self.pinned),
            'reply_count': self.reply_count or 0,
            'created_at': self.created_at.strftime('%m-%d %H:%M') if self.created_at else '',
            'replies': replies or [],
        }


class DiscussionReply(db.Model):
    __tablename__ = 'lms_replies'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    post_id = db.Column(db.Integer, db.ForeignKey('lms_posts.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_ai = db.Column(db.Boolean, default=False)
    ai_model = db.Column(db.String(80), nullable=True)
    content = db.Column(db.Text, nullable=False, default='')
    citations = db.Column(db.Text, nullable=True)                    # JSON
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'is_ai': bool(self.is_ai),
            'ai_model': self.ai_model or '',
            'content': self.content,
            'citations': _loads(self.citations, []),
            'created_at': self.created_at.strftime('%m-%d %H:%M') if self.created_at else '',
        }


class Signin(db.Model):
    __tablename__ = 'lms_signins'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(db.Integer, db.ForeignKey('lms_courses.id', ondelete='CASCADE'), nullable=False, index=True)
    code = db.Column(db.String(10), nullable=False)
    title = db.Column(db.String(120), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    expire_minutes = db.Column(db.Integer, default=10)
    closed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def expired(self):
        if not self.created_at:
            return True
        from datetime import timedelta
        return datetime.now() > self.created_at + timedelta(minutes=self.expire_minutes or 10)

    def to_dict(self, checked=None, total=0):
        return {
            'id': self.id,
            'course_id': self.course_id,
            'code': self.code,
            'title': self.title or '课堂签到',
            'closed': bool(self.closed),
            'expired': self.expired(),
            'expire_minutes': self.expire_minutes or 10,
            'checked': checked,
            'total': total,
            'created_at': self.created_at.strftime('%m-%d %H:%M') if self.created_at else '',
        }


class SigninRecord(db.Model):
    __tablename__ = 'lms_signin_records'
    __table_args__ = (db.UniqueConstraint('signin_id', 'user_id', name='uq_lms_signin'),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    signin_id = db.Column(db.Integer, db.ForeignKey('lms_signins.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Notice(db.Model):
    __tablename__ = 'lms_notices'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(db.Integer, db.ForeignKey('lms_courses.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False, default='')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    pinned = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def to_dict(self, read=False):
        return {
            'id': self.id,
            'course_id': self.course_id,
            'title': self.title,
            'content': self.content,
            'pinned': bool(self.pinned),
            'read': bool(read),
            'created_at': self.created_at.strftime('%m-%d %H:%M') if self.created_at else '',
        }


class NoticeRead(db.Model):
    __tablename__ = 'lms_notice_reads'
    __table_args__ = (db.UniqueConstraint('notice_id', 'user_id', name='uq_lms_notice_read'),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    notice_id = db.Column(db.Integer, db.ForeignKey('lms_notices.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)