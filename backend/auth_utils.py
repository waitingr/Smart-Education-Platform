"""认证与权限工具（独立模块，避免 blueprint 之间循环导入）。"""
import re
from functools import wraps

from flask import jsonify, request, session


def validate_email(email):
    return re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email or '') is not None


def get_current_user_from_request():
    """支持三种身份来源：Flask session、Bearer Token、匿名会话。"""
    from models import User

    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user:
            return user

    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
        if token:
            user = User.query.filter_by(api_token=token).first()
            if user and user.verify_api_token(token):
                return user
    return None


def login_required(handler):
    @wraps(handler)
    def decorated(*args, **kwargs):
        user = get_current_user_from_request()
        if not user:
            return jsonify({'code': 401, 'message': '请先登录'}), 401
        request.current_user = user
        return handler(*args, **kwargs)
    return decorated


def teacher_required(handler):
    @wraps(handler)
    def decorated(*args, **kwargs):
        user = get_current_user_from_request()
        if not user:
            return jsonify({'code': 401, 'message': '请先登录'}), 401
        if user.role not in ('teacher', 'admin'):
            return jsonify({'code': 403, 'message': '仅教师可操作'}), 403
        request.current_user = user
        return handler(*args, **kwargs)
    return decorated


def optional_user(handler):
    """允许游客访问，但把登录用户注入 request.current_user。"""
    @wraps(handler)
    def decorated(*args, **kwargs):
        request.current_user = get_current_user_from_request()
        return handler(*args, **kwargs)
    return decorated