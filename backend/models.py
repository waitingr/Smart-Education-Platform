from datetime import datetime, timedelta
from extensions import db
import bcrypt
import secrets

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
    # Token for cross-origin auth (file:// frontend)
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
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S')
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