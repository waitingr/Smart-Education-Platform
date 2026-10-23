# routes.py
from flask import Blueprint, request, jsonify, session, Response, stream_with_context
from models import User, Homework, HomeworkSubmission, ChatHistory
from extensions import db
import re
import sys
import os
import time
import uuid
import tempfile
from werkzeug.utils import secure_filename
from functools import wraps

# ============ 上传配置 ============
# 统一使用项目根目录下的 data 目录，避免在不同机器/目录下失效
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
VIDEO_DIR = os.path.join(DATA_DIR, 'video')
os.makedirs(VIDEO_DIR, exist_ok=True)
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.webm', '.avi', '.mov', '.mkv', '.flv', '.wmv'}

# 课件资料存储目录：项目根目录下的 data/materials 文件夹
MATERIAL_DIR = os.path.join(DATA_DIR, 'materials')
os.makedirs(MATERIAL_DIR, exist_ok=True)
ATTACHMENT_DIR = os.getenv('ATTACHMENT_DIR', os.path.join(DATA_DIR, 'attachments'))
os.makedirs(ATTACHMENT_DIR, exist_ok=True)
ALLOWED_ATTACHMENT_EXTENSIONS = {'.txt', '.pdf', '.doc', '.docx'}
MAX_ATTACHMENT_TEXT_LENGTH = 30000
ALLOWED_MATERIAL_EXTENSIONS = {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.txt', '.md', '.zip', '.rar', '.7z', '.png', '.jpg', '.jpeg'}

# 文件类型 → 图标、颜色映射
MATERIAL_ICONS = {
    '.pdf': ('ph-file-pdf', 'text-red-500'),
    '.doc': ('ph-file-doc', 'text-blue-500'),
    '.docx': ('ph-file-doc', 'text-blue-500'),
    '.ppt': ('ph-file-ppt', 'text-orange-500'),
    '.pptx': ('ph-file-ppt', 'text-orange-500'),
    '.xls': ('ph-file-xls', 'text-green-500'),
    '.xlsx': ('ph-file-xls', 'text-green-500'),
    '.txt': ('ph-file-text', 'text-gray-500'),
    '.md': ('ph-file-md', 'text-indigo-500'),
    '.zip': ('ph-file-zip', 'text-yellow-500'),
    '.rar': ('ph-file-archive', 'text-yellow-500'),
    '.7z': ('ph-file-archive', 'text-yellow-500'),
    '.png': ('ph-file-image', 'text-purple-500'),
    '.jpg': ('ph-file-image', 'text-purple-500'),
    '.jpeg': ('ph-file-image', 'text-purple-500'),
}

# ============ 导入 RAG 服务 ============
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for candidate in (PROJECT_ROOT, os.path.abspath(os.path.join(PROJECT_ROOT, '..'))):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

try:
    from rag_service import rag_query, init_rag, stop_generation
    RAG_AVAILABLE = True
    # 启动时初始化 RAG（会在后台加载模型）
    import threading
    threading.Thread(target=init_rag, daemon=True).start()
    print("RAG service initialized in background...")
except ImportError as e:
    RAG_AVAILABLE = False
    print(f"⚠️ RAG 服务导入失败：{e}")

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/attachments/upload', methods=['POST'])
def upload_attachment():
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'code': 400, 'message': '未选择附件'}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        return jsonify({'code': 400, 'message': '仅支持 txt、pdf、word 文件'}), 400
    if request.content_length and request.content_length > 20 * 1024 * 1024:
        return jsonify({'code': 400, 'message': '附件不能超过 20MB'}), 400
    original_name = os.path.basename(file.filename).replace('\x00', '')
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', original_name).strip('. ')
    if not safe_name:
        safe_name = f'{uuid.uuid4().hex}{ext}'
    unique_name = safe_name
    save_path = os.path.join(ATTACHMENT_DIR, unique_name)
    if os.path.exists(save_path):
        stem, suffix = os.path.splitext(safe_name)
        unique_name = f'{stem}_{uuid.uuid4().hex[:8]}{suffix}'
        save_path = os.path.join(ATTACHMENT_DIR, unique_name)
    file.save(save_path)
    return jsonify({'code': 200, 'message': '附件上传成功', 'data': {'name': safe_name, 'filename': unique_name}}), 200


def extract_attachment_text(filename):
    """Extract a bounded amount of text for the local model prompt."""
    safe_filename = os.path.basename(filename or '')
    if not safe_filename:
        return ''
    file_path = os.path.join(ATTACHMENT_DIR, safe_filename)
    if not os.path.isfile(file_path):
        raise ValueError('附件不存在，请重新上传')

    extension = os.path.splitext(safe_filename)[1].lower()
    if extension == '.txt':
        with open(file_path, 'r', encoding='utf-8-sig', errors='replace') as attachment_file:
            return attachment_file.read(MAX_ATTACHMENT_TEXT_LENGTH)
    if extension == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError('服务器缺少 PDF 解析依赖，请安装 pypdf') from error
        reader = PdfReader(file_path)
        pages = []
        text_length = 0
        for page in reader.pages:
            page_text = page.extract_text() or ''
            remaining = MAX_ATTACHMENT_TEXT_LENGTH - text_length
            if remaining <= 0:
                break
            pages.append(page_text[:remaining])
            text_length += len(pages[-1])
        return '\n\n'.join(pages)
    if extension == '.docx':
        try:
            from docx import Document
        except ImportError as error:
            raise ValueError('服务器缺少 Word 解析依赖，请安装 python-docx') from error
        document = Document(file_path)
        paragraphs = []
        text_length = 0
        for paragraph in document.paragraphs:
            remaining = MAX_ATTACHMENT_TEXT_LENGTH - text_length
            if remaining <= 0:
                break
            paragraph_text = paragraph.text[:remaining]
            paragraphs.append(paragraph_text)
            text_length += len(paragraph_text)
        return '\n'.join(paragraphs)
    if extension == '.doc':
        raise ValueError('暂不支持读取旧版 .doc 内容，请另存为 .docx 后上传')
    raise ValueError('不支持的附件类型')


def save_chat_history(prompt, response, mode):
    user = get_current_user_from_request()
    db.session.add(ChatHistory(user_id=user.id if user else None, session_id=request.headers.get('X-Session-Id'), title=prompt[:40], prompt=prompt, response=response, mode=mode))
    db.session.commit()


def get_current_user_from_request():
    """从请求中获取当前用户（支持 session 或 API Token）"""
    # 1. 尝试从 Flask session 获取（同域名情况）
    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user:
            return user
    
    # 2. 尝试从 API Token 获取（跨域/file:// 情况）
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]  # 去掉 'Bearer '
        user = User.query.filter_by(api_token=token).first()
        if user and user.verify_api_token(token):
            return user
    
    # 3. 尝试从 X-Session-Id 获取（兼容旧版前端）
    session_id = request.headers.get('X-Session-Id')
    if session_id:
        # 这里可以扩展：如果有映射关系的话
        pass
    
    return None


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user_from_request()
        if not user:
            return jsonify({'code': 401, 'message': '请先登录'}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated_function


def teacher_required(f):
    """教师权限验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user_from_request()
        if not user:
            return jsonify({'code': 401, 'message': '请先登录'}), 401
        if user.role not in ('teacher', 'admin'):
            return jsonify({'code': 403, 'message': '仅教师可操作'}), 403
        request.current_user = user
        return f(*args, **kwargs)
    return decorated_function


def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email)


@auth_bp.route('/chat/stop', methods=['POST'])
def stop_chat():
    """停止当前进程中的模型生成。"""
    if RAG_AVAILABLE:
        stop_generation()
    return jsonify({'code': 200, 'message': '已发送停止生成信号'}), 200


# ==================== 认证接口 ====================

@auth_bp.route('/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        login_identifier = data.get('username', '').strip()
        password = data.get('password', '').strip()
        login_role = data.get('role', 'student').strip()
        
        if not login_identifier or not password:
            return jsonify({'code': 400, 'message': '请填写完整的登录信息'}), 400

        if login_role == 'student':
            user = User.query.filter_by(student_number=login_identifier, role='student').first()
        elif login_role == 'teacher':
            user = User.query.filter(User.employee_number == login_identifier, User.role.in_(('teacher', 'admin'))).first()
        else:
            return jsonify({'code': 400, 'message': '无效的登录类型'}), 400
        
        if not user:
            return jsonify({'code': 401, 'message': '用户名或密码错误'}), 401
        
        if not user.check_password(password):
            return jsonify({'code': 401, 'message': '用户名或密码错误'}), 401

        session['user_id'] = user.id
        session['username'] = user.username
        api_token = user.generate_api_token()
        db.session.commit()
        
        return jsonify({
            'code': 200,
            'message': '登录成功',
            'data': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'student_number': user.student_number,
                'employee_number': user.employee_number,
                'role': user.role,
                'api_token': api_token
            }
        }), 200
        
    except Exception as e:
        return jsonify({'code': 500, 'message': f'服务器错误: {str(e)}'}), 500


@auth_bp.route('/auth/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        student_number = data.get('student_number', '').strip()
        employee_number = data.get('employee_number', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'student').strip()
        
        if not username or not email or not password or (role == 'student' and not student_number) or (role == 'teacher' and not employee_number):
            return jsonify({'code': 400, 'message': '请填写完整的注册信息'}), 400
        
        if len(password) < 6:
            return jsonify({'code': 400, 'message': '密码长度至少为6位'}), 400
        
        if not validate_email(email):
            return jsonify({'code': 400, 'message': '请输入有效的邮箱地址'}), 400
        
        if role not in ('student', 'teacher'):
            return jsonify({'code': 400, 'message': '无效的用户角色'}), 400
        
        if User.query.filter_by(username=username).first():
            return jsonify({'code': 400, 'message': '用户名已存在'}), 400
        
        if User.query.filter_by(email=email).first():
            return jsonify({'code': 400, 'message': '邮箱已被注册'}), 400
        if student_number and User.query.filter_by(student_number=student_number).first():
            return jsonify({'code': 400, 'message': '学号已被注册'}), 400
        if employee_number and User.query.filter_by(employee_number=employee_number).first():
            return jsonify({'code': 400, 'message': '教职工号已被注册'}), 400
        
        new_user = User(username=username, student_number=student_number or None, employee_number=employee_number or None, email=email, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        session['user_id'] = new_user.id
        session['username'] = new_user.username
        api_token = new_user.generate_api_token()
        db.session.commit()
        
        return jsonify({
            'code': 200,
            'message': '注册成功',
            'data': {
                'id': new_user.id,
                'username': new_user.username,
                'student_number': new_user.student_number,
                'employee_number': new_user.employee_number,
                'email': new_user.email,
                'role': new_user.role,
                'api_token': api_token
            }
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'code': 500, 'message': f'服务器错误: {str(e)}'}), 500


@auth_bp.route('/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'code': 200, 'message': '退出成功'}), 200


@auth_bp.route('/auth/user', methods=['GET'])
def get_current_user():
    user = get_current_user_from_request()
    if not user:
        return jsonify({'code': 401, 'message': '未登录'}), 401
    
    return jsonify({
        'code': 200,
        'message': 'success',
        'data': {
            'id': user.id,
            'username': user.username,
            'student_number': user.student_number,
            'employee_number': user.employee_number,
            'email': user.email,
            'role': user.role,
            'api_token': user.api_token
        }
    }), 200


@auth_bp.route('/auth/profile', methods=['PATCH'])
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    user = request.current_user

    if not username or not email or not validate_email(email):
        return jsonify({'code': 400, 'message': '用户名和邮箱格式不正确'}), 400
    if User.query.filter(User.username == username, User.id != user.id).first():
        return jsonify({'code': 400, 'message': '用户名已存在'}), 400
    if User.query.filter(User.email == email, User.id != user.id).first():
        return jsonify({'code': 400, 'message': '邮箱已被注册'}), 400
    if new_password:
        if not current_password or not user.check_password(current_password):
            return jsonify({'code': 400, 'message': '原密码不正确'}), 400
        if len(new_password) < 6:
            return jsonify({'code': 400, 'message': '新密码长度至少为6位'}), 400
        user.set_password(new_password)
    user.username = username
    user.email = email
    session['username'] = username
    db.session.commit()
    return jsonify({'code': 200, 'message': '账号信息更新成功', 'data': {'id': user.id, 'username': user.username, 'email': user.email, 'role': user.role, 'api_token': user.api_token}}), 200


@auth_bp.route('/students/<student_number>', methods=['GET'])
@teacher_required
def get_student_info(student_number):
    student = User.query.filter_by(student_number=student_number, role='student').first()
    if not student:
        return jsonify({'code': 404, 'message': '未找到对应学生'}), 404
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'id': student.id,
        'student_number': student.student_number,
        'username': student.username,
        'email': student.email
    }}), 200


@auth_bp.route('/students/<student_number>', methods=['PATCH'])
@teacher_required
def update_student_info(student_number):
    student = User.query.filter_by(student_number=student_number, role='student').first()
    if not student:
        return jsonify({'code': 404, 'message': '未找到对应学生'}), 404

    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    if not username or not email or not validate_email(email):
        return jsonify({'code': 400, 'message': '姓名和邮箱格式不正确'}), 400
    if password and len(password) < 6:
        return jsonify({'code': 400, 'message': '密码长度至少为6位'}), 400
    if User.query.filter(User.username == username, User.id != student.id).first():
        return jsonify({'code': 400, 'message': '姓名已被使用'}), 400
    if User.query.filter(User.email == email, User.id != student.id).first():
        return jsonify({'code': 400, 'message': '邮箱已被注册'}), 400

    student.username = username
    student.email = email
    if password:
        student.set_password(password)
    db.session.commit()
    return jsonify({'code': 200, 'message': '学生信息已更新', 'data': {
        'id': student.id,
        'student_number': student.student_number,
        'username': student.username,
        'email': student.email
    }}), 200


# ==================== AI 接口（调用 RAG 服务） ====================

@auth_bp.route('/chat', methods=['POST'])
def chat():
    """通用 AI 聊天接口（支持连接检测）"""
    try:
        data = request.get_json()
        prompt = data.get('prompt', '').strip()
        mode = data.get('mode', 'qa')
        attachment_name = os.path.basename(data.get('attachment_name', '').strip())
        
        if not prompt:
            return jsonify({'code': 400, 'message': '请输入问题'}), 400

        if attachment_name:
            try:
                attachment_text = extract_attachment_text(attachment_name)
            except ValueError as error:
                return jsonify({'code': 400, 'message': str(error)}), 400
            if not attachment_text.strip():
                return jsonify({'code': 400, 'message': '附件中没有可提取的文字'}), 400
            prompt = f'{prompt}\n\n请根据以下附件内容回答：\n---附件内容开始---\n{attachment_text}\n---附件内容结束---'
        
        print("Received chat request:", prompt[:50] + "...")
        
        # 检测客户端是否仍然连接
        def check_connection():
            # 如果客户端断开，会抛出异常
            if not request.environ.get('wsgi.websocket'):
                # 检查客户端是否断开连接
                if hasattr(request, 'environ') and 'werkzeug.server.shutdown' in request.environ:
                    return
            return True
        
        if RAG_AVAILABLE:
            try:
                # 检查连接状态
                if not check_connection():
                    print("⚠️ 客户端已断开连接")
                    return jsonify({'code': 499, 'message': '客户端已断开'}), 499
                
                result = rag_query(prompt)
                
                # 再次检查连接状态
                if not check_connection():
                    print("⚠️ 客户端已断开连接（生成完成后）")
                    return jsonify({'code': 499, 'message': '客户端已断开'}), 499
                
                content = result.get('answer', '⚠️ 生成回答为空')
                sources = result.get('sources', [])
            except Exception as e:
                import traceback
                print("rag_query execution error:", traceback.format_exc())
                content = f"⚠️ RAG 服务执行出错：{str(e)}"
                sources = []
        else:
            content = f"⚠️ RAG 服务未就绪。您的问题是：{prompt}"
            sources = []
        
        save_chat_history(prompt, content, mode)
        return jsonify({
            'code': 200,
            'message': 'success',
            'data': {
                'content': content,
                'sources': sources,
                'mode': mode
            }
        }), 200
        
    except Exception as e:
        import traceback
        print("chat overall error:", traceback.format_exc())
        return jsonify({'code': 500, 'message': str(e)}), 500


@auth_bp.route('/chat/stream', methods=['POST'])
def chat_stream():
    """流式聊天接口（支持中断停止）"""
    try:
        data = request.get_json()
        prompt = data.get('prompt', '').strip()
        mode = data.get('mode', 'qa')
        
        if not prompt:
            return jsonify({'code': 400, 'message': '请输入问题'}), 400
        
        print("📡 Received stream chat request:", prompt[:50] + "...")
        
        if not RAG_AVAILABLE:
            def fallback_generate():
                yield f"data: ⚠️ RAG 服务未就绪。您的问题是：{prompt}\n\n"
                yield "data: [DONE]\n\n"
            return Response(fallback_generate(), mimetype='text/event-stream')
        
        def generate():
            try:
                print("📡 开始流式生成...")
                
                # 调用 RAG 服务
                result = rag_query(prompt)
                answer = result.get('answer', '')
                
                # 逐字输出（按句子分块输出）
                chunk_size = 20  # 每20个字符输出一次
                for i in range(0, len(answer), chunk_size):
                    # 检查客户端是否断开（通过检测生成器是否被关闭）
                    chunk = answer[i:i+chunk_size]
                    yield f"data: {chunk}\n\n"
                    
                    # 模拟流式延迟
                    time.sleep(0.02)
                
                # 发送结束标志
                yield "data: [DONE]\n\n"
                print("📡 流式生成完成")
                
            except GeneratorExit:
                # 客户端断开连接时，停止生成
                print("⏹️ 客户端断开，停止流式生成")
                return
            except Exception as e:
                import traceback
                print("❌ Stream error:", traceback.format_exc())
                yield f"data: ❌ 生成回答时出错：{str(e)}\n\n"
                yield "data: [DONE]\n\n"
        
        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'  # 禁用 Nginx 缓冲
            }
        )
        
    except Exception as e:
        import traceback
        print("chat_stream overall error:", traceback.format_exc())
        return jsonify({'code': 500, 'message': str(e)}), 500


@auth_bp.route('/code/evaluate', methods=['POST'])
def code_evaluate():
    """代码评测接口（基于 RAG）"""
    try:
        data = request.get_json()
        prompt = data.get('prompt', '').strip()
        mode = data.get('mode', 'code')
        
        if not prompt:
            return jsonify({'code': 400, 'message': '请输入代码'}), 400
        
        print(f"📥 收到代码评测请求：{prompt[:50]}...")
        
        # 构建代码评测专用 Prompt
        code_prompt = f"""请对以下代码进行评测：
1. 分析时间复杂度
2. 指出潜在问题
3. 给出优化建议

代码：
{prompt}
"""
        
        if RAG_AVAILABLE:
            try:
                result = rag_query(code_prompt)
                content = result.get('answer', '⚠️ 生成回答为空')
                sources = result.get('sources', [])
            except Exception as e:
                import traceback
                print("rag_query execution error:", traceback.format_exc())
                content = f"⚠️ RAG 服务执行出错：{str(e)}"
                sources = []
        else:
            print("⚠️ RAG_AVAILABLE 为 False，使用模拟回复")
            content = f"### 💻 代码评测（模拟）\n\n您提交的代码片段：\n```\n{prompt[:300]}...\n```\n\n> 完整评测功能开发中，当前为模拟回复。"
            sources = []
        
        save_chat_history(prompt, content, mode)
        return jsonify({
            'code': 200,
            'message': 'success',
            'data': {
                'content': content,
                'sources': sources,
                'mode': mode
            }
        }), 200
        
    except Exception as e:
        import traceback
        print(f"❌ code_evaluate 整体错误：{traceback.format_exc()}")
        return jsonify({'code': 500, 'message': str(e)}), 500


@auth_bp.route('/diagnosis', methods=['POST'])
def diagnosis():
    """学情诊断接口（基于 RAG）"""
    try:
        data = request.get_json()
        prompt = data.get('prompt', '').strip()
        mode = data.get('mode', 'diagnosis')
        
        if not prompt:
            return jsonify({'code': 400, 'message': '请输入诊断需求'}), 400
        
        print(f"📥 收到诊断请求：{prompt[:50]}...")
        
        # 构建学情诊断专用 Prompt
        diag_prompt = f"""请根据以下信息生成学情诊断报告：
1. 分析整体学习情况
2. 识别知识薄弱点
3. 给出教学建议

用户输入：
{prompt}
"""
        
        if RAG_AVAILABLE:
            try:
                result = rag_query(diag_prompt)
                content = result.get('answer', '⚠️ 生成回答为空')
                sources = result.get('sources', [])
            except Exception as e:
                import traceback
                print("rag_query execution error:", traceback.format_exc())
                content = f"⚠️ RAG 服务执行出错：{str(e)}"
                sources = []
        else:
            print("⚠️ RAG_AVAILABLE 为 False，使用模拟回复")
            content = "### 📊 学情诊断报告（模拟）\n\n诊断功能开发中，当前为模拟回复。\n\n您输入的需求：" + prompt
            sources = []
        
        save_chat_history(prompt, content, mode)
        return jsonify({
            'code': 200,
            'message': 'success',
            'data': {
                'content': content,
                'sources': sources,
                'mode': mode
            }
        }), 200
        
    except Exception as e:
        import traceback
        print(f"❌ diagnosis 整体错误：{traceback.format_exc()}")
        return jsonify({'code': 500, 'message': str(e)}), 500


# ==================== 历史记录接口 ====================

@auth_bp.route('/history', methods=['GET'])
def get_history():
    user = get_current_user_from_request()
    query = ChatHistory.query.filter_by(user_id=user.id) if user else ChatHistory.query.filter_by(session_id=request.headers.get('X-Session-Id'))
    records = query.order_by(ChatHistory.created_at.desc()).limit(50).all()
    return jsonify({'code': 200, 'message': 'success', 'data': [{'id': item.id, 'title': item.title, 'mode': item.mode, 'created_at': item.created_at.strftime('%Y-%m-%d %H:%M')} for item in records]}), 200


@auth_bp.route('/history/<int:history_id>', methods=['GET'])
def get_history_detail(history_id):
    user = get_current_user_from_request()
    item = ChatHistory.query.get(history_id)
    if user:
        is_owner = item and item.user_id == user.id
    else:
        is_owner = item and item.user_id is None and item.session_id == request.headers.get('X-Session-Id')
    if not is_owner:
        return jsonify({'code': 404, 'message': '历史记录不存在'}), 404
    return jsonify({'code': 200, 'message': 'success', 'data': {'id': item.id, 'title': item.title, 'prompt': item.prompt, 'response': item.response, 'mode': item.mode}}), 200


@auth_bp.route('/history/<int:history_id>', methods=['DELETE'])
def delete_history(history_id):
    user = get_current_user_from_request()
    item = ChatHistory.query.get(history_id)
    if user:
        is_owner = item and item.user_id == user.id
    else:
        is_owner = item and item.user_id is None and item.session_id == request.headers.get('X-Session-Id')
    if not is_owner:
        return jsonify({'code': 404, 'message': '历史记录不存在'}), 404

    try:
        db.session.delete(item)
        db.session.commit()
        return jsonify({'code': 200, 'message': '历史记录已删除'}), 200
    except Exception as error:
        db.session.rollback()
        return jsonify({'code': 500, 'message': f'删除历史记录失败：{error}'}), 500


# ==================== 资源接口 ====================

@auth_bp.route('/videos', methods=['POST'])
def get_videos():
    """获取视频列表（动态扫描 video/ 目录）"""
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    video_root = os.path.abspath(VIDEO_DIR)
    current_dir = os.path.abspath(os.path.join(VIDEO_DIR, folder))
    if not current_dir.startswith(video_root + os.sep) and current_dir != video_root:
        return jsonify({'code': 400, 'message': '文件夹路径无效'}), 400
    videos = []
    folders = []
    if os.path.isdir(current_dir):
        for idx, fname in enumerate(sorted(os.listdir(current_dir)), start=1):
            filepath = os.path.join(current_dir, fname)
            if os.path.isdir(filepath):
                folders.append({'name': fname, 'path': os.path.join(folder, fname).replace('\\', '/')})
                continue
            if os.path.isfile(filepath):
                ext = os.path.splitext(fname)[1].lower()
                if ext in ALLOWED_VIDEO_EXTENSIONS:
                    size_mb = os.path.getsize(filepath) / (1024 * 1024)
                    videos.append({
                        'id': idx,
                        'title': os.path.splitext(fname)[0],
                        'filename': fname,
                        'size': f'{size_mb:.1f}MB',
                        'duration': '',   # 可由前端或后续分析补充
                        'progress': 0,
                        'thumbnail': '🎬'
                    })
    return jsonify({'code': 200, 'message': 'success', 'data': {'videos': videos, 'folders': folders, 'folder': folder}}), 200


@auth_bp.route('/videos/upload', methods=['POST'])
@teacher_required
def upload_video():
    """上传视频文件到 video/ 目录（仅教师角色）"""
    try:
        if 'file' not in request.files:
            return jsonify({'code': 400, 'message': '未选择文件'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'code': 400, 'message': '文件名为空'}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_VIDEO_EXTENSIONS:
            allowed = ', '.join(ALLOWED_VIDEO_EXTENSIONS)
            return jsonify({'code': 400, 'message': f'不支持的文件格式，允许：{allowed}'}), 400

        folder = request.form.get('folder', '').strip().strip('/\\')
        video_root = os.path.abspath(VIDEO_DIR)
        target_dir = os.path.abspath(os.path.join(VIDEO_DIR, folder))
        if not target_dir.startswith(video_root + os.sep) and target_dir != video_root:
            return jsonify({'code': 400, 'message': '文件夹路径无效'}), 400
        if not os.path.isdir(target_dir):
            return jsonify({'code': 404, 'message': '文件夹不存在'}), 404

        # 使用安全文件名，重名时追加短后缀
        safe_name = secure_filename(file.filename)
        unique_name = safe_name or f'{uuid.uuid4().hex}{ext}'
        save_path = os.path.join(target_dir, unique_name)
        if os.path.exists(save_path):
            stem, suffix = os.path.splitext(unique_name)
            unique_name = f'{stem}_{uuid.uuid4().hex[:8]}{suffix}'
            save_path = os.path.join(target_dir, unique_name)
        file.save(save_path)

        print(f"✅ 视频上传成功：{safe_name} → {unique_name}")

        return jsonify({
            'code': 200,
            'message': '上传成功',
            'data': {
                'id': len(os.listdir(target_dir)),
                'title': os.path.splitext(safe_name)[0],
                'filename': unique_name,
                'folder': folder,
                'size': f'{os.path.getsize(save_path) / (1024 * 1024):.1f}MB',
            }
        }), 200

    except Exception as e:
        import traceback
        print("❌ 视频上传错误:", traceback.format_exc())
        return jsonify({'code': 500, 'message': f'上传失败：{str(e)}'}), 500


@auth_bp.route('/videos/folders', methods=['POST'])
@teacher_required
def create_video_folder():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    parent = data.get('parent', '').strip().strip('/\\')
    if not name or name in ('.', '..') or re.search(r'[<>:"/\\|?*]', name):
        return jsonify({'code': 400, 'message': '文件夹名称无效'}), 400
    video_root = os.path.abspath(VIDEO_DIR)
    folder_path = os.path.abspath(os.path.join(VIDEO_DIR, parent, name))
    if not folder_path.startswith(video_root + os.sep) or os.path.exists(folder_path):
        return jsonify({'code': 400, 'message': '文件夹路径无效或已存在'}), 400
    os.makedirs(folder_path)
    return jsonify({'code': 200, 'message': '文件夹创建成功'}), 200


@auth_bp.route('/videos/rename', methods=['PATCH'])
@teacher_required
def rename_video():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    filename = data.get('filename', '').strip()
    new_name = data.get('new_name', '').strip()
    video_root = os.path.abspath(VIDEO_DIR)
    source = os.path.abspath(os.path.join(VIDEO_DIR, folder, filename))
    if not filename or not new_name or not source.startswith(video_root + os.sep) or not os.path.isfile(source):
        return jsonify({'code': 404, 'message': '视频文件不存在'}), 404
    ext = os.path.splitext(filename)[1]
    if not os.path.splitext(new_name)[1]:
        new_name += ext
    safe_name = secure_filename(new_name)
    target = os.path.abspath(os.path.join(os.path.dirname(source), safe_name))
    if not safe_name or not target.startswith(video_root + os.sep) or os.path.exists(target):
        return jsonify({'code': 400, 'message': '新文件名无效或已存在'}), 400
    os.rename(source, target)
    return jsonify({'code': 200, 'message': '视频重命名成功'}), 200


@auth_bp.route('/videos/delete', methods=['DELETE'])
@teacher_required
def delete_video():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    filename = data.get('filename', '').strip()
    video_root = os.path.abspath(VIDEO_DIR)
    target = os.path.abspath(os.path.join(VIDEO_DIR, folder, filename))
    if not filename or not target.startswith(video_root + os.sep) or not os.path.isfile(target):
        return jsonify({'code': 404, 'message': '视频文件不存在'}), 404
    os.remove(target)
    return jsonify({'code': 200, 'message': '视频删除成功'}), 200


@auth_bp.route('/videos/folders', methods=['PATCH'])
@teacher_required
def rename_video_folder():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    new_name = data.get('new_name', '').strip()
    video_root = os.path.abspath(VIDEO_DIR)
    source = os.path.abspath(os.path.join(VIDEO_DIR, folder))
    if not folder or not new_name or not source.startswith(video_root + os.sep) or not os.path.isdir(source):
        return jsonify({'code': 404, 'message': '文件夹不存在'}), 404
    if new_name in ('.', '..') or re.search(r'[<>:"/\\|?*]', new_name):
        return jsonify({'code': 400, 'message': '新文件夹名称无效'}), 400
    target = os.path.abspath(os.path.join(os.path.dirname(source), new_name))
    if not target.startswith(video_root + os.sep) or os.path.exists(target):
        return jsonify({'code': 400, 'message': '新文件夹名称无效或已存在'}), 400
    os.rename(source, target)
    return jsonify({'code': 200, 'message': '文件夹重命名成功'}), 200


@auth_bp.route('/videos/folders', methods=['DELETE'])
@teacher_required
def delete_video_folder():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    video_root = os.path.abspath(VIDEO_DIR)
    target = os.path.abspath(os.path.join(VIDEO_DIR, folder))
    if not folder or not target.startswith(video_root + os.sep) or not os.path.isdir(target):
        return jsonify({'code': 404, 'message': '文件夹不存在'}), 404
    if os.listdir(target):
        return jsonify({'code': 400, 'message': '文件夹不为空，请先删除其中内容'}), 400
    os.rmdir(target)
    return jsonify({'code': 200, 'message': '文件夹删除成功'}), 200


@auth_bp.route('/materials', methods=['POST'])
def get_materials():
    """获取课件列表（动态扫描 materials/ 目录）"""
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    current_dir = os.path.abspath(os.path.join(MATERIAL_DIR, folder))
    if not current_dir.startswith(os.path.abspath(MATERIAL_DIR) + os.sep) and current_dir != os.path.abspath(MATERIAL_DIR):
        return jsonify({'code': 400, 'message': '文件夹路径无效'}), 400
    materials = []
    folders = []
    if os.path.isdir(current_dir):
        for idx, fname in enumerate(sorted(os.listdir(current_dir)), start=1):
            filepath = os.path.join(current_dir, fname)
            if os.path.isdir(filepath):
                folders.append({'name': fname, 'path': os.path.join(folder, fname).replace('\\', '/')})
                continue
            if os.path.isfile(filepath):
                ext = os.path.splitext(fname)[1].lower()
                if ext in ALLOWED_MATERIAL_EXTENSIONS:
                    size_bytes = os.path.getsize(filepath)
                    size_str = f'{size_bytes / (1024 * 1024):.1f}MB' if size_bytes >= 1024 * 1024 else f'{size_bytes / 1024:.0f}KB'
                    icon, color = MATERIAL_ICONS.get(ext, ('ph-file', 'text-gray-500'))
                    materials.append({
                        'id': idx,
                        'name': os.path.splitext(fname)[0],
                        'filename': fname,
                        'ext': ext.lstrip('.'),
                        'type': ext.lstrip('.'),
                        'size': size_str,
                        'icon': icon,
                        'color': color,
                    })
    return jsonify({'code': 200, 'message': 'success', 'data': {'materials': materials, 'folders': folders, 'folder': folder}}), 200


@auth_bp.route('/materials/folders', methods=['POST'])
@teacher_required
def create_material_folder():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    parent = data.get('parent', '').strip().strip('/\\')
    if not name or name in ('.', '..') or re.search(r'[<>:"/\\|?*]', name):
        return jsonify({'code': 400, 'message': '文件夹名称无效'}), 400
    folder_path = os.path.abspath(os.path.join(MATERIAL_DIR, parent, name))
    material_root = os.path.abspath(MATERIAL_DIR)
    if not folder_path.startswith(material_root + os.sep):
        return jsonify({'code': 400, 'message': '文件夹路径无效'}), 400
    if os.path.exists(folder_path):
        return jsonify({'code': 400, 'message': '文件夹已存在'}), 400
    os.makedirs(folder_path)
    return jsonify({'code': 200, 'message': '文件夹创建成功'}), 200


@auth_bp.route('/materials/delete', methods=['DELETE'])
@teacher_required
def delete_material():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    filename = data.get('filename', '').strip()
    target = os.path.abspath(os.path.join(MATERIAL_DIR, folder, filename))
    material_root = os.path.abspath(MATERIAL_DIR)
    if not filename or not target.startswith(material_root + os.sep) or not os.path.isfile(target):
        return jsonify({'code': 404, 'message': '文件不存在'}), 404
    os.remove(target)
    return jsonify({'code': 200, 'message': '文件删除成功'}), 200


@auth_bp.route('/materials/folders', methods=['DELETE'])
@teacher_required
def delete_material_folder():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    target = os.path.abspath(os.path.join(MATERIAL_DIR, folder))
    material_root = os.path.abspath(MATERIAL_DIR)
    if not folder or not target.startswith(material_root + os.sep) or not os.path.isdir(target):
        return jsonify({'code': 404, 'message': '文件夹不存在'}), 404
    if os.listdir(target):
        return jsonify({'code': 400, 'message': '文件夹不为空，请先删除其中内容'}), 400
    os.rmdir(target)
    return jsonify({'code': 200, 'message': '文件夹删除成功'}), 200


@auth_bp.route('/materials/rename', methods=['PATCH'])
@teacher_required
def rename_material():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    filename = data.get('filename', '').strip()
    new_name = data.get('new_name', '').strip()
    material_root = os.path.abspath(MATERIAL_DIR)
    source = os.path.abspath(os.path.join(MATERIAL_DIR, folder, filename))
    if not filename or not new_name or not source.startswith(material_root + os.sep) or not os.path.isfile(source):
        return jsonify({'code': 404, 'message': '文件不存在'}), 404
    if not os.path.splitext(new_name)[1]:
        new_name += os.path.splitext(filename)[1]
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', os.path.basename(new_name)).strip('. ')
    target = os.path.abspath(os.path.join(os.path.dirname(source), safe_name))
    if not safe_name or not target.startswith(material_root + os.sep) or os.path.exists(target):
        return jsonify({'code': 400, 'message': '新文件名无效或已存在'}), 400
    os.rename(source, target)
    return jsonify({'code': 200, 'message': '文件重命名成功'}), 200


@auth_bp.route('/materials/folders', methods=['PATCH'])
@teacher_required
def rename_material_folder():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder', '').strip().strip('/\\')
    new_name = data.get('new_name', '').strip()
    material_root = os.path.abspath(MATERIAL_DIR)
    source = os.path.abspath(os.path.join(MATERIAL_DIR, folder))
    if not folder or not new_name or not source.startswith(material_root + os.sep) or not os.path.isdir(source):
        return jsonify({'code': 404, 'message': '文件夹不存在'}), 404
    if new_name in ('.', '..') or re.search(r'[<>:"/\\|?*]', new_name):
        return jsonify({'code': 400, 'message': '新文件夹名称无效'}), 400
    target = os.path.abspath(os.path.join(os.path.dirname(source), new_name))
    if not target.startswith(material_root + os.sep) or os.path.exists(target):
        return jsonify({'code': 400, 'message': '新文件夹名称无效或已存在'}), 400
    os.rename(source, target)
    return jsonify({'code': 200, 'message': '文件夹重命名成功'}), 200


@auth_bp.route('/materials/upload', methods=['POST'])
@teacher_required
def upload_material():
    """上传课件资料到 materials/ 目录（仅教师角色）"""
    try:
        if 'file' not in request.files:
            return jsonify({'code': 400, 'message': '未选择文件'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'code': 400, 'message': '文件名为空'}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_MATERIAL_EXTENSIONS:
            allowed = ', '.join(ALLOWED_MATERIAL_EXTENSIONS)
            return jsonify({'code': 400, 'message': f'不支持的文件格式，允许：{allowed}'}), 400

        folder = request.form.get('folder', '').strip().strip('/\\')
        target_dir = os.path.abspath(os.path.join(MATERIAL_DIR, folder))
        material_root = os.path.abspath(MATERIAL_DIR)
        if not target_dir.startswith(material_root + os.sep) and target_dir != material_root:
            return jsonify({'code': 400, 'message': '文件夹路径无效'}), 400
        if not os.path.isdir(target_dir):
            return jsonify({'code': 404, 'message': '文件夹不存在'}), 404

        # 保留原始文件名，同时移除路径和 Windows 不允许的字符
        original_name = os.path.basename(file.filename).replace('\x00', '')
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', original_name).strip('. ')
        if not safe_name:
            safe_name = f'{uuid.uuid4().hex}{ext}'
        unique_name = safe_name
        save_path = os.path.join(target_dir, unique_name)
        if os.path.exists(save_path):
            stem, suffix = os.path.splitext(safe_name)
            unique_name = f'{stem}_{uuid.uuid4().hex[:8]}{suffix}'
            save_path = os.path.join(target_dir, unique_name)
        file.save(save_path)

        print(f"✅ 课件上传成功：{safe_name} → {unique_name}")

        size_bytes = os.path.getsize(save_path)
        size_str = f'{size_bytes / (1024 * 1024):.1f}MB' if size_bytes >= 1024 * 1024 else f'{size_bytes / 1024:.0f}KB'
        icon, color = MATERIAL_ICONS.get(ext, ('ph-file', 'text-gray-500'))

        return jsonify({
            'code': 200,
            'message': '上传成功',
            'data': {
                'id': len(os.listdir(target_dir)),
                'name': os.path.splitext(safe_name)[0],
                'filename': unique_name,
                'folder': folder,
                'ext': ext.lstrip('.'),
                'size': size_str,
                'icon': icon,
                'color': color,
            }
        }), 200

    except Exception as e:
        import traceback
        print("❌ 课件上传错误:", traceback.format_exc())
        return jsonify({'code': 500, 'message': f'上传失败：{str(e)}'}), 500


@auth_bp.route('/homeworks', methods=['POST'])
def get_homeworks():
    """获取作业列表"""
    current_user = get_current_user_from_request()
    student_count = User.query.filter_by(role='student').count()
    homeworks = [
        {'id': homework.id, 'title': homework.title, 'description': homework.description or '', 'deadline': homework.deadline,
         'status': ('graded' if student_submission and student_submission.score is not None else 'submitted') if (student_submission := (HomeworkSubmission.query.filter_by(homework_id=homework.id, student_id=current_user.id).first() if current_user else None)) else 'pending',
         'score': student_submission.score if student_submission else None, 'comment': student_submission.comment if student_submission else '',
         'completed_count': HomeworkSubmission.query.filter_by(homework_id=homework.id).count(), 'total_students': student_count}
        for homework in Homework.query.order_by(Homework.created_at.desc()).all()
    ]
    return jsonify({'code': 200, 'message': 'success', 'data': homeworks}), 200


@auth_bp.route('/homeworks/create', methods=['POST'])
@teacher_required
def create_homework():
    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    deadline = data.get('deadline', '').strip()
    description = data.get('description', '').strip()
    if not title or not deadline:
        return jsonify({'code': 400, 'message': '请填写作业标题和截止时间'}), 400
    homework = Homework(title=title, description=description, deadline=deadline, created_by=request.current_user.id)
    db.session.add(homework)
    db.session.commit()
    return jsonify({'code': 200, 'message': '作业发布成功', 'data': {'id': homework.id}}), 200


@auth_bp.route('/homeworks/<int:homework_id>/submit', methods=['POST'])
@login_required
def submit_homework(homework_id):
    homework = Homework.query.get(homework_id)
    if not homework:
        return jsonify({'code': 404, 'message': '作业不存在'}), 404
    if request.current_user.role != 'student':
        return jsonify({'code': 403, 'message': '只有学生可以提交作业'}), 403
    data = request.get_json(silent=True) or {}
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'code': 400, 'message': '请填写作业内容'}), 400
    submission = HomeworkSubmission.query.filter_by(homework_id=homework_id, student_id=request.current_user.id).first()
    if submission:
        return jsonify({'code': 400, 'message': '你已经提交过这份作业'}), 400
    db.session.add(HomeworkSubmission(homework_id=homework_id, student_id=request.current_user.id, content=content))
    db.session.commit()
    return jsonify({'code': 200, 'message': '作业提交成功'}), 200


@auth_bp.route('/homeworks/<int:homework_id>/submissions', methods=['GET'])
@teacher_required
def get_homework_submissions(homework_id):
    submissions = HomeworkSubmission.query.filter_by(homework_id=homework_id).order_by(HomeworkSubmission.submitted_at.asc()).all()
    return jsonify({'code': 200, 'message': 'success', 'data': [{'id': item.id, 'student_id': item.student_id, 'student_name': User.query.get(item.student_id).username, 'content': item.content, 'score': item.score, 'comment': item.comment or ''} for item in submissions]}), 200


@auth_bp.route('/homeworks/<int:homework_id>/submissions/<int:submission_id>', methods=['PATCH'])
@teacher_required
def grade_homework_submission(homework_id, submission_id):
    data = request.get_json(silent=True) or {}
    score = data.get('score')
    comment = data.get('comment', '').strip()
    if not isinstance(score, int) or score < 0 or score > 100:
        return jsonify({'code': 400, 'message': '分数必须是 0 到 100 的整数'}), 400
    submission = HomeworkSubmission.query.filter_by(id=submission_id, homework_id=homework_id).first()
    if not submission:
        return jsonify({'code': 404, 'message': '提交记录不存在'}), 404
    submission.score = score
    submission.comment = comment
    db.session.commit()
    return jsonify({'code': 200, 'message': '批改已保存'}), 200


@auth_bp.route('/homeworks/<int:homework_id>', methods=['DELETE'])
@teacher_required
def delete_homework(homework_id):
    homework = Homework.query.get(homework_id)
    if not homework:
        return jsonify({'code': 404, 'message': '作业不存在'}), 404
    HomeworkSubmission.query.filter_by(homework_id=homework_id).delete()
    db.session.delete(homework)
    db.session.commit()
    return jsonify({'code': 200, 'message': '作业删除成功'}), 200


@auth_bp.route('/knowledge-base/status', methods=['GET'])
def get_kb_status():
    """获取知识库状态"""
    return jsonify({
        'code': 200,
        'message': 'success',
        'data': {
            'status': 'connected' if RAG_AVAILABLE else 'disconnected',
            'rag_available': RAG_AVAILABLE
        }
    }), 200