from datetime import datetime, timedelta

import bcrypt
import secrets

from extensions import db


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    student_number = db.Column(db.String(50), unique=True, nullable=True)
    employee_number = db.Column(db.String(50), unique=True, nullable=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='student')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    api_token = db.Column(db.String(64), unique=True, nullable=True)
    token_expires = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    def generate_api_token(self, expires_days=30):
        """生成用于跨域认证的 API Token"""
        self.api_token = secrets.token_urlsafe(32)
        self.token_expires = datetime.now() + timedelta(days=expires_days)
        return self.api_token

    def verify_api_token(self, token):
        """验证 API Token"""
        if not self.api_token or not self.token_expires:
            return False
        if datetime.now() > self.token_expires:
            return False
        return secrets.compare_digest(self.api_token, token)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'student_number': self.student_number,
            'employee_number': self.employee_number,
            'email': self.email,
            'role': self.role,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
        }


class Homework(db.Model):
    __tablename__ = 'homeworks'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    deadline = db.Column(db.String(50), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class HomeworkSubmission(db.Model):
    __tablename__ = 'homework_submissions'
    __table_args__ = (db.UniqueConstraint('homework_id', 'student_id', name='unique_homework_student'),)

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    homework_id = db.Column(db.Integer, db.ForeignKey('homeworks.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False, default='')
    score = db.Column(db.Integer, nullable=True)
    comment = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.now)


class ChatHistory(db.Model):
    __tablename__ = 'chat_histories'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    session_id = db.Column(db.String(100), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    response = db.Column(db.Text, nullable=False)
    mode = db.Column(db.String(30), nullable=False, default='qa')
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)


# ==================== 多轮会话 ====================
class Conversation(db.Model):
    __tablename__ = 'conversations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    session_id = db.Column(db.String(120), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False, default='新的对话')
    agent_id = db.Column(db.String(50), nullable=False, default='tutor')
    scenario = db.Column(db.String(20), nullable=False, default='learn')
    message_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'agent_id': self.agent_id,
            'scenario': self.scenario,
            'message_count': self.message_count or 0,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else '',
        }


class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default='user')
    content = db.Column(db.Text, nullable=False, default='')
    agent_id = db.Column(db.String(50), nullable=True)
    citations = db.Column(db.Text, nullable=True)   # JSON
    meta = db.Column(db.Text, nullable=True)        # JSON：trace / provider / degraded ...
    ai_generated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def to_dict(self):
        import json
        return {
            'id': self.id,
            'role': self.role,
            'content': self.content,
            'agent_id': self.agent_id,
            'citations': json.loads(self.citations) if self.citations else [],
            'meta': json.loads(self.meta) if self.meta else {},
            'ai_generated': bool(self.ai_generated),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else '',
        }


# ==================== 知识库文档登记 ====================
class KnowledgeDoc(db.Model):
    __tablename__ = 'knowledge_docs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    doc_id = db.Column(db.String(32), unique=True, nullable=True)
    title = db.Column(db.String(200), nullable=False)
    source_type = db.Column(db.String(30), nullable=False, default='upload')
    authors = db.Column(db.String(200), nullable=True)
    year = db.Column(db.String(20), nullable=True)
    publisher = db.Column(db.String(200), nullable=True)
    authority = db.Column(db.Integer, default=3)
    filename = db.Column(db.String(255), nullable=True)
    chunk_count = db.Column(db.Integer, default=0)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'doc_id': self.doc_id,
            'title': self.title,
            'source_type': self.source_type,
            'authors': self.authors or '',
            'year': self.year or '',
            'publisher': self.publisher or '',
            'authority': self.authority or 3,
            'chunk_count': self.chunk_count or 0,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }


# ==================== 个性化画像 ====================
class UserProfile(db.Model):
    __tablename__ = 'user_profiles'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    level = db.Column(db.String(20), default='intermediate')     # beginner / intermediate / advanced
    goal = db.Column(db.String(200), nullable=True)
    learning_style = db.Column(db.String(20), default='balanced')  # socratic / direct / balanced
    detail_level = db.Column(db.String(20), default='standard')    # brief / standard / deep
    weak_points = db.Column(db.Text, nullable=True)                # JSON list
    strong_points = db.Column(db.Text, nullable=True)              # JSON list
    interactions = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def weak_list(self):
        import json
        try:
            return json.loads(self.weak_points or '[]')
        except ValueError:
            return []

    def to_dict(self):
        return {
            'level': self.level,
            'goal': self.goal or '',
            'learning_style': self.learning_style,
            'detail_level': self.detail_level,
            'weak_points': self.weak_list(),
            'interactions': self.interactions or 0,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M') if self.updated_at else '',
        }


# ==================== 反馈与使用行为（效果验证） ====================
class Feedback(db.Model):
    __tablename__ = 'feedbacks'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    conversation_id = db.Column(db.Integer, nullable=True, index=True)
    message_id = db.Column(db.Integer, nullable=True)
    agent_id = db.Column(db.String(50), nullable=True, index=True)
    scenario = db.Column(db.String(20), nullable=True)
    rating = db.Column(db.Integer, default=0)       # -1 不满意 / 0 中立 / 1 满意
    usefulness = db.Column(db.Integer, default=0)   # 1~5 星
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'agent_id': self.agent_id,
            'scenario': self.scenario,
            'rating': self.rating,
            'usefulness': self.usefulness,
            'comment': self.comment or '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }


class UsageEvent(db.Model):
    __tablename__ = 'usage_events'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    event_type = db.Column(db.String(40), nullable=False, default='agent_run')
    agent_id = db.Column(db.String(50), nullable=True, index=True)
    scenario = db.Column(db.String(20), nullable=True)
    latency_ms = db.Column(db.Integer, default=0)
    degraded = db.Column(db.Boolean, default=False)
    success = db.Column(db.Boolean, default=True)
    role = db.Column(db.String(20), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)


class AgentRun(db.Model):
    __tablename__ = 'agent_runs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    agent_id = db.Column(db.String(50), nullable=False, index=True)
    scenario = db.Column(db.String(20), nullable=True)
    inputs = db.Column(db.Text, nullable=True)      # JSON
    answer = db.Column(db.Text, nullable=True)
    trace = db.Column(db.Text, nullable=True)       # JSON
    citations = db.Column(db.Text, nullable=True)   # JSON
    latency_ms = db.Column(db.Integer, default=0)
    degraded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)


class EvaluationRun(db.Model):
    __tablename__ = 'evaluation_runs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False, default='default')
    dataset = db.Column(db.String(200), nullable=True)
    total = db.Column(db.Integer, default=0)
    passed = db.Column(db.Integer, default=0)
    metrics = db.Column(db.Text, nullable=True)     # JSON
    report_path = db.Column(db.String(300), nullable=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def to_dict(self):
        import json
        return {
            'id': self.id,
            'name': self.name,
            'total': self.total,
            'passed': self.passed,
            'accuracy': round((self.passed or 0) / self.total, 4) if self.total else 0,
            'metrics': json.loads(self.metrics) if self.metrics else {},
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else '',
        }