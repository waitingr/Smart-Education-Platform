# app.py
import os
import sys

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from sqlalchemy import inspect, text
from werkzeug.exceptions import HTTPException

from config import Config
from extensions import db


class _SafeStream:
    """stdout/stderr 容错包装。

    Windows 下控制台句柄失效（如关闭运行窗口/控制台被释放）后，print 会抛出 OSError，
    连带弄崩聊天接口的响应。包装后写入异常被吞掉，保证日志输出不影响 HTTP 请求。
    """

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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')

app = Flask(__name__, static_folder=None)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']
app.json.ensure_ascii = False

# 允许跨域及携带 Cookie/Session（前端也可直接从 file:// 打开调试）
CORS(app, supports_credentials=True, origins='*')

db.init_app(app)

# ==================== 文件存储与访问配置 ====================
VIDEO_DIR = os.path.join(DATA_DIR, 'video')
MATERIAL_DIR = os.path.join(DATA_DIR, 'materials')
ATTACHMENT_DIR = os.getenv('ATTACHMENT_DIR', os.path.join(DATA_DIR, 'attachments'))
for directory in (VIDEO_DIR, MATERIAL_DIR, ATTACHMENT_DIR, Config.KB_DOC_DIR, Config.KB_CACHE_DIR, Config.EVAL_DIR):
    os.makedirs(directory, exist_ok=True)


def _migrate_schema():
    """轻量向前兼容迁移：老库缺列/缺索引时自动补齐。"""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if 'homework_submissions' in tables:
        columns = {column['name'] for column in inspector.get_columns('homework_submissions')}
        for column_name, column_type in {'content': 'TEXT', 'score': 'INTEGER NULL', 'comment': 'TEXT NULL'}.items():
            if column_name not in columns:
                db.session.execute(text(f'ALTER TABLE homework_submissions ADD COLUMN {column_name} {column_type}'))
        db.session.commit()

    if 'users' in tables:
        columns = {column['name'] for column in inspector.get_columns('users')}
        for column_name in ('student_number', 'employee_number'):
            if column_name not in columns:
                db.session.execute(text(f'ALTER TABLE users ADD COLUMN {column_name} VARCHAR(50) NULL'))
        db.session.commit()
        is_sqlite = db.engine.dialect.name == 'sqlite'
        indexes = {index['name'] for index in inspector.get_indexes('users')}
        if is_sqlite:
            db.session.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_student_number ON users (student_number)'))
            db.session.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_employee_number ON users (employee_number)'))
        else:
            if 'uq_users_student_number' not in indexes:
                db.session.execute(text('CREATE UNIQUE INDEX uq_users_student_number ON users (student_number)'))
            if 'uq_users_employee_number' not in indexes:
                db.session.execute(text('CREATE UNIQUE INDEX uq_users_employee_number ON users (employee_number)'))
        db.session.commit()


with app.app_context():
    from routes import auth_bp
    from api_ext import ext_bp
    from lms_routes import lms_bp

    app.register_blueprint(auth_bp, url_prefix='/api/v1')
    app.register_blueprint(ext_bp, url_prefix='/api/v1')
    app.register_blueprint(lms_bp, url_prefix='/api/v1/lms')
    db.create_all()
    try:
        _migrate_schema()
    except Exception as error:  # 迁移失败不阻塞启动
        db.session.rollback()
        print(f'[db] 结构迁移跳过：{error}')


def _warm_services():
    """后台预热知识库，避免首问等待；失败仅记录日志。"""
    with app.app_context():
        try:
            from kb_service import get_kb
            from llm_client import get_llm

            stats = get_kb().stats()
            health = get_llm().health()
            print(f"[启动] 知识库：{stats['doc_count']} 篇文档 / {stats['chunk_count']} 个知识块"
                  f"（向量后端 {stats['backend']}）")
            print(f"[启动] 大模型：{health['provider']} / {health['model']}"
                  f"（{'已接入' if health['available'] else '离线检索模式'}）")
        except Exception as error:
            print(f'[启动] 服务预热失败：{error}')


# ==================== 健康检查 ====================
@app.route('/api/v1/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'message': f'{Config.PLATFORM_NAME}后端服务运行正常',
        'discipline': Config.DISCIPLINE_NAME,
        'course': Config.DISCIPLINE_COURSE,
        'database': db.engine.dialect.name,
    }), 200


@app.route('/api/v1/meta', methods=['GET'])
def meta():
    """平台元信息：模块清单、能力矩阵，供前端与作品说明复用。"""
    from agents import SCENARIOS, agent_catalog

    scenarios = [
        {'id': key, 'label': value['name'], 'icon': value['icon'], 'desc': value['desc']}
        for key, value in SCENARIOS.items()
    ]
    flat_agents = [agent for group in agent_catalog() for agent in group['agents']]
    return jsonify({
        'code': 200,
        'message': 'success',
        'data': {
            'platform': Config.PLATFORM_NAME,
            'discipline': Config.DISCIPLINE_NAME,
            'course': Config.DISCIPLINE_COURSE,
            'ai_label': Config.AI_CONTENT_LABEL,
            'scenarios': scenarios,
            'agents': flat_agents,
            'capabilities': [
                {'id': 'kb', 'name': '学科知识库构建', 'detail': '结构化元数据 + 章节级分块 + 混合检索'},
                {'id': 'lms', 'name': '教学闭环', 'detail': '课程任务点 / 随堂测验 / 讨论区 / 签到 / 通知，AI 全程参与'},
                {'id': 'rag', 'name': '检索增强生成', 'detail': 'BM25 + 向量混合召回，逐句引用标注'},
                {'id': 'workflow', 'name': '智能工作流编排', 'detail': '多步骤 + 条件分支 + 执行轨迹留痕'},
                {'id': 'finetune', 'name': '模型微调', 'detail': 'LoRA / SFT 训练脚本与学科指令数据集'},
                {'id': 'multimodal', 'name': '多模态交互', 'detail': 'PDF / Word / Excel / 图片解析与生成'},
                {'id': 'personalize', 'name': '个性化自适应', 'detail': '学习者画像驱动的讲解策略与路径调整'},
                {'id': 'eval', 'name': '效果评测', 'detail': '12 题标准测试集 + 引用覆盖率 + 准确率报告'},
                {'id': 'compliance', 'name': '合规与溯源', 'detail': 'AI 生成标识 + 学术诚信守卫 + 出处定位'},
            ],
        },
    }), 200


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """未捕获异常统一返回 JSON（携带 CORS 头）。"""
    try:
        import traceback
        print('未处理的服务器异常:', traceback.format_exc())
    except Exception:
        pass
    if isinstance(error, HTTPException):
        return jsonify({'code': error.code, 'message': error.description}), error.code
    return jsonify({'code': 500, 'message': f'服务器内部错误：{error}'}), 500


# ==================== 资源访问 ====================
@app.route('/video/<path:filename>', methods=['GET'])
def serve_video(filename):
    return send_from_directory(VIDEO_DIR, filename)


@app.route('/materials/<path:filename>', methods=['GET'])
def serve_material(filename):
    return send_from_directory(MATERIAL_DIR, filename, as_attachment=False)


# ==================== 前端托管（同源部署，避免跨域与 Cookie 问题） ====================
@app.route('/', methods=['GET'])
def serve_index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/<path:filename>', methods=['GET'])
def serve_frontend(filename):
    target = os.path.join(FRONTEND_DIR, filename)
    if os.path.isfile(target):
        return send_from_directory(FRONTEND_DIR, filename)
    # 支持前端路由回退到单页应用
    if '.' not in os.path.basename(filename):
        return send_from_directory(FRONTEND_DIR, 'index.html')
    return jsonify({'code': 404, 'message': '资源不存在'}), 404


if __name__ == '__main__':
    import threading

    threading.Thread(target=_warm_services, daemon=True).start()
    port = int(os.getenv('PORT', '5000'))
    print('=' * 72)
    print(f'{Config.PLATFORM_NAME} · {Config.DISCIPLINE_NAME}学科垂类大模型平台')
    print(f'  前端入口：http://127.0.0.1:{port}/')
    print(f'  接口文档：http://127.0.0.1:{port}/api/v1/meta')
    print(f'  健康检查：http://127.0.0.1:{port}/api/v1/health')
    print('=' * 72)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)