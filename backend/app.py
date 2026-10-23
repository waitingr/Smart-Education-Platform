# app.py
from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
from config import Config
from extensions import db
import os
import sys
import tempfile
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from sqlalchemy import inspect, text


class _SafeStream:
    """stdout/stderr 容错包装。
    Windows 下控制台句柄失效（如关闭运行窗口/控制台被释放）后，
    print 会抛出 OSError: [Errno 22] 并连带弄崩聊天接口的响应。
    包装后写入异常被吞掉，保证日志输出不影响 HTTP 请求。"""

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        try:
            self._stream.write(data)
            self._stream.flush()
        except Exception:
            pass
        return len(data) if data else 0

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.stdout = _SafeStream(sys.stdout)
sys.stderr = _SafeStream(sys.stderr)

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')

# 允许跨域及携带 Cookie/Session
CORS(app, supports_credentials=True, origins='*')

db.init_app(app)

with app.app_context():
    from routes import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/api/v1')
    db.create_all()
    submission_columns = {column['name'] for column in inspect(db.engine).get_columns('homework_submissions')}
    for column_name, column_type in {'content': 'TEXT', 'score': 'INT NULL', 'comment': 'TEXT NULL'}.items():
        if column_name not in submission_columns:
            db.session.execute(text(f'ALTER TABLE homework_submissions ADD COLUMN {column_name} {column_type}'))
    db.session.commit()
    user_columns = {column['name'] for column in inspect(db.engine).get_columns('users')}
    if 'student_number' not in user_columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN student_number VARCHAR(50) NULL'))
        db.session.commit()
    user_columns = {column['name'] for column in inspect(db.engine).get_columns('users')}
    if 'employee_number' not in user_columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN employee_number VARCHAR(50) NULL'))
        db.session.commit()
    user_indexes = {index['name'] for index in inspect(db.engine).get_indexes('users')}
    if 'uq_users_student_number' not in user_indexes:
        db.session.execute(text('CREATE UNIQUE INDEX uq_users_student_number ON users (student_number)'))
        db.session.commit()
    user_indexes = {index['name'] for index in inspect(db.engine).get_indexes('users')}
    if 'uq_users_employee_number' not in user_indexes:
        db.session.execute(text('CREATE UNIQUE INDEX uq_users_employee_number ON users (employee_number)'))
        db.session.commit()

@app.route('/api/v1/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'message': '灵眸智教后端服务运行正常'}), 200


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """未捕获异常统一返回 JSON（携带 CORS 头）。
    否则 Werkzeug 调试器 HTML 页面会被浏览器跨域策略拦截，
    前端表现为 "Failed to fetch" 而无法看到真实错误。"""
    try:
        import traceback
        print('未处理的服务器异常:', traceback.format_exc())
    except Exception:
        pass
    if isinstance(error, HTTPException):
        return jsonify({'code': error.code, 'message': error.description}), error.code
    return jsonify({'code': 500, 'message': f'服务器内部错误：{error}'}), 500

# ==================== 文件存储与访问配置 ====================
# 资源目录统一放在项目根目录下的 data 文件夹
VIDEO_DIR = os.path.join(DATA_DIR, 'video')
MATERIAL_DIR = os.path.join(DATA_DIR, 'materials')
ATTACHMENT_DIR = os.getenv('ATTACHMENT_DIR', os.path.join(DATA_DIR, 'attachments'))

# 确保文件夹存在，避免因目录不存在导致保存文件时报错
os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(MATERIAL_DIR, exist_ok=True)
os.makedirs(ATTACHMENT_DIR, exist_ok=True)

# ==================== 文件访问接口 ====================
@app.route('/video/<path:filename>', methods=['GET'])
def serve_video(filename):
    return send_from_directory(VIDEO_DIR, filename)

@app.route('/materials/<path:filename>', methods=['GET'])
def serve_material(filename):
    # as_attachment=False 允许在浏览器中直接预览（如 PDF/图片）
    return send_from_directory(MATERIAL_DIR, filename, as_attachment=False)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)