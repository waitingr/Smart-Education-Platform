# routes.py
from flask import Blueprint, request, jsonify, session, Response, stream_with_context
from models import (AgentRun, ChatHistory, Conversation, Feedback, Homework, HomeworkSubmission,
                    Message, User, UserProfile)
from auth_utils import (get_current_user_from_request, login_required, optional_user,
                        teacher_required, validate_email)
from extensions import db
import json
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
ALLOWED_ATTACHMENT_EXTENSIONS = {'.txt', '.pdf', '.doc', '.docx', '.md', '.csv',
                                 '.xlsx', '.xls', '.pptx', '.png', '.jpg', '.jpeg', '.bmp', '.webp'}
MAX_ATTACHMENT_TEXT_LENGTH = 30000


def attachment_text_limit():
    """本机小模型上下文有限：按模型上下文预算截断附件正文，而不是让它整段塞不进去。"""
    try:
        from llm_client import get_llm
        provider = get_llm().provider
    except Exception:
        provider = 'offline'
    if provider in ('ollama', 'local'):
        return max(1500, min(MAX_ATTACHMENT_TEXT_LENGTH, Config.LLM_CONTEXT_CHARS))
    return MAX_ATTACHMENT_TEXT_LENGTH
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

# ============ 导入学科智能体 / 知识库 / 大模型 ============
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for candidate in (PROJECT_ROOT, os.path.abspath(os.path.join(PROJECT_ROOT, '..'))):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

from agents import AGENTS, check_academic_integrity, run_agent  # noqa: E402
from config import Config  # noqa: E402
from kb_service import get_kb  # noqa: E402
from llm_client import get_llm  # noqa: E402
from telemetry import record_agent_run, record_event  # noqa: E402

RAG_AVAILABLE = True
try:
    from rag_service import stop_generation
except ImportError:  # pragma: no cover
    RAG_AVAILABLE = False

    def stop_generation():
        return None

# 兼容旧模式名 → 智能体
MODE_AGENT_MAP = {'qa': 'tutor', 'code': 'tutor', 'diagnosis': 'class_diagnosis', 'chat': 'tutor'}

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
    if extension in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
        # 图片走视觉模型：识别成文字后与普通附件一样参与问答
        from vision_service import get_vision
        vision = get_vision()
        if not vision.available:
            raise ValueError('未配置视觉模型（VLM），图片暂时无法识别；'
                             '可配置 VISION_PROVIDER=ollama 后重试')
        description, info = vision.describe(file_path)
        if not description:
            raise ValueError(f'图片识别失败：{info.get("error") or "视觉模型未返回内容"}')
        return f'【图片内容识别 · {info.get("model", "VLM")}】\n{description}'[:attachment_text_limit()]
    # 其余文档（md / csv / xlsx / pptx / html 等）统一交给文档解析器，避免"能传不能读"
    try:
        from doc_parser import SUPPORTED_ALL, parse_file
    except ImportError:
        SUPPORTED_ALL, parse_file = set(), None
    if parse_file is not None and extension in SUPPORTED_ALL:
        parsed = parse_file(file_path)
        if getattr(parsed, 'text', '').strip():
            return parsed.text[:attachment_text_limit()]
        raise ValueError('文档中没有可提取的文字')
    raise ValueError('不支持的附件类型')


def save_chat_history(prompt, response, mode):
    user = get_current_user_from_request()
    db.session.add(ChatHistory(user_id=user.id if user else None, session_id=request.headers.get('X-Session-Id'), title=prompt[:40], prompt=prompt, response=response, mode=mode))
    db.session.commit()


def _profile_snapshot(user):
    """读取用户画像（无画像时返回默认值，游客按中等水平处理）。"""
    if not user:
        return {'level': 'intermediate', 'goal': '', 'learning_style': 'balanced',
                'detail_level': 'standard', 'weak_points': [], 'interactions': 0}
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        return {'level': 'intermediate', 'goal': '', 'learning_style': 'balanced',
                'detail_level': 'standard', 'weak_points': [], 'interactions': 0}
    return profile.to_dict()


def _conversation_turn(prompt, mode='qa', agent_id=None, conversation_id=None,
                       attachment_name=None, extra_inputs=None, title_hint=None):
    """执行一轮多轮对话：定位会话 → 载入历史 → 运行智能体 → 落库 → 埋点。"""
    user = get_current_user_from_request()
    agent_id = agent_id or MODE_AGENT_MAP.get(mode, 'tutor')
    if agent_id not in AGENTS:
        agent_id = 'tutor'

    conversation = None
    if conversation_id:
        try:
            conversation = Conversation.query.get(int(conversation_id))
        except (TypeError, ValueError):
            conversation = None
        if conversation and user and conversation.user_id not in (None, user.id):
            conversation = None
    if conversation is None:
        conversation = Conversation(
            user_id=getattr(user, 'id', None),
            session_id=request.headers.get('X-Session-Id'),
            title=(title_hint or prompt or '新的对话')[:80],
            agent_id=agent_id,
            scenario=AGENTS[agent_id]['scenario'],
        )
        db.session.add(conversation)
        db.session.commit()

    history = [
        {'role': item.role, 'content': item.content}
        for item in reversed(
            Message.query.filter_by(conversation_id=conversation.id)
            .order_by(Message.id.desc()).limit(8).all()
        )
    ]

    inputs = dict(extra_inputs or {})
    inputs.setdefault('question', prompt)
    payload = run_agent(agent_id, inputs, user=user, profile=_profile_snapshot(user),
                        history=history, session_id=request.headers.get('X-Session-Id'))

    db.session.add(Message(conversation_id=conversation.id, role='user', agent_id=agent_id, content=prompt))
    ai_message = Message(
        conversation_id=conversation.id,
        role='assistant',
        agent_id=agent_id,
        content=payload.get('answer', ''),
        citations=json.dumps(payload.get('citations') or [], ensure_ascii=False),
        meta=json.dumps({
            'trace': payload.get('trace'),
            'provider': payload.get('provider'),
            'degraded': payload.get('degraded'),
            'artifacts': payload.get('artifacts'),
            'facts': payload.get('facts'),
            'latency_ms': payload.get('latency_ms'),
            'ai_label': Config.AI_CONTENT_LABEL,
        }, ensure_ascii=False),
        ai_generated=True,
    )
    db.session.add(ai_message)
    conversation.message_count = (conversation.message_count or 0) + 2
    db.session.commit()

    record_agent_run(user, payload, inputs)
    record_event(user, 'chat', agent_id=agent_id, scenario=AGENTS[agent_id]['scenario'],
                 latency_ms=payload.get('latency_ms'), degraded=payload.get('degraded'),
                 detail={'mode': mode})
    save_chat_history(prompt, payload.get('answer', ''), mode)

    payload.update({
        'conversation_id': conversation.id,
        'message_id': ai_message.id,
        'ai_generated': True,
        'ai_label': Config.AI_CONTENT_LABEL,
    })
    return payload


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
        login_identifier = (data.get('username') or data.get('identifier') or data.get('email') or '').strip()
        password = data.get('password', '').strip()
        login_role = data.get('role', 'student').strip()
        
        if not login_identifier or not password:
            return jsonify({'code': 400, 'message': '请填写完整的登录信息'}), 400

        if '@' in login_identifier:
            # 支持用邮箱登录（教师与学生均可）
            user = User.query.filter_by(email=login_identifier).first()
        elif login_role == 'student':
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


# ==================== AI 接口（学科智能体工作流） ====================

def _build_chat_inputs(data):
    """把前端请求解析成智能体输入。返回 (prompt, mode, agent_id, conversation_id, extra_inputs)。"""
    prompt = (data.get('prompt') or '').strip()
    mode = data.get('mode') or 'qa'
    agent_id = data.get('agent_id')
    conversation_id = data.get('conversation_id')
    attachment_name = os.path.basename((data.get('attachment_name') or '').strip())
    extra_inputs = {}

    if mode == 'code':
        prompt = ('请评审下面这段代码，按「正确性 → 时间复杂度 → 空间复杂度 → 边界处理 → 风格与可读性 → 优化建议」'
                  '逐项输出，并给出改进后的代码：\n```\n' + prompt + '\n```')
        extra_inputs['socratic'] = False

    if attachment_name:
        extension = os.path.splitext(attachment_name)[1].lower()
        attachment_path = os.path.join(ATTACHMENT_DIR, attachment_name)
        if extension in ('.png', '.jpg', '.jpeg', '.bmp', '.webp'):
            # 图片不走"先转文字再问答"：直接交给能看图的生成模型
            extra_inputs.update({
                'attachment_name': attachment_name,
                'attachment_path': attachment_path,
            })
            prompt = f'{prompt}\n\n（本轮附带图片「{attachment_name}」，请直接看图作答，并指明你从图中看到的依据）'
        else:
            attachment_text = extract_attachment_text(attachment_name)
            if not attachment_text.strip():
                raise ValueError('附件中没有可提取的文字')
            if agent_id == 'data_analysis' or mode == 'analysis':
                extra_inputs.update({
                    'attachment_name': attachment_name,
                    'analysis_type': data.get('analysis_type') or '描述统计',
                    'context': prompt,
                })
            else:
                # 附件正文不再塞进 prompt（会污染检索查询），而是作为可引用的材料交给工作流
                extra_inputs.update({
                    'attachment_name': attachment_name,
                    'attachment_text': attachment_text,
                })
                prompt = f'{prompt}\n\n（本轮已附带附件「{attachment_name}」，请结合附件内容作答并标注出处）'
    return prompt, mode, agent_id, conversation_id, extra_inputs


@auth_bp.route('/chat', methods=['POST'])
def chat():
    """多轮对话接口：会话续接 + 知识库检索 + 引用溯源 + 个性化。"""
    data = request.get_json(silent=True) or {}
    raw_prompt = (data.get('prompt') or '').strip()
    if not raw_prompt:
        return jsonify({'code': 400, 'message': '请输入问题'}), 400
    try:
        prompt, mode, agent_id, conversation_id, extra_inputs = _build_chat_inputs(data)
    except ValueError as error:
        return jsonify({'code': 400, 'message': str(error)}), 400

    guard = check_academic_integrity(raw_prompt)
    if guard['blocked']:
        return jsonify({'code': 403, 'message': f"请求涉及学术诚信风险（{guard['reason']}），已拒绝生成。"}), 403

    try:
        payload = _conversation_turn(prompt, mode=mode, agent_id=agent_id,
                                     conversation_id=conversation_id,
                                     extra_inputs=extra_inputs)
    except Exception as error:
        import traceback
        print('chat error:', traceback.format_exc())
        return jsonify({'code': 500, 'message': f'服务异常：{error}'}), 500

    return jsonify({'code': 200, 'message': 'success', 'data': {
        'content': payload.get('answer', ''),
        'sources': [item['locator'] for item in (payload.get('citations') or [])],
        'citations': payload.get('citations') or [],
        'trace': payload.get('trace') or [],
        'facts': payload.get('facts') or [],
        'artifacts': payload.get('artifacts') or [],
        'suggestions': payload.get('suggestions') or [],
        'mode': mode,
        'agent_id': payload.get('agent_id'),
        'conversation_id': payload.get('conversation_id'),
        'message_id': payload.get('message_id'),
        'degraded': payload.get('degraded'),
        'provider': payload.get('provider'),
        'latency_ms': payload.get('latency_ms'),
        'low_confidence': payload.get('low_confidence', False),
        'ai_generated': True,
        'ai_label': Config.AI_CONTENT_LABEL,
    }}), 200


@auth_bp.route('/chat/stream', methods=['POST'])
def chat_stream():
    """流式对话接口：先返回检索状态，再逐段推送正文，最后推送引用与执行轨迹。"""
    data = request.get_json(silent=True) or {}
    raw_prompt = (data.get('prompt') or '').strip()
    if not raw_prompt:
        return jsonify({'code': 400, 'message': '请输入问题'}), 400
    try:
        prompt, mode, agent_id, conversation_id, extra_inputs = _build_chat_inputs(data)
    except ValueError as error:
        return jsonify({'code': 400, 'message': str(error)}), 400

    def generate():
        yield 'event: status\ndata: ' + json.dumps({'stage': '检索学科知识库'}, ensure_ascii=False) + '\n\n'
        try:
            payload = _conversation_turn(prompt, mode=mode, agent_id=agent_id,
                                         conversation_id=conversation_id,
                                         extra_inputs=extra_inputs)
            answer = payload.get('answer', '')
            step = 28
            for start in range(0, len(answer), step):
                delta = answer[start:start + step]
                yield 'event: delta\ndata: ' + json.dumps({'text': delta}, ensure_ascii=False) + '\n\n'
            meta = {
                'citations': payload.get('citations') or [],
                'trace': payload.get('trace') or [],
                'conversation_id': payload.get('conversation_id'),
                'message_id': payload.get('message_id'),
                'degraded': payload.get('degraded'),
                'provider': payload.get('provider'),
                'latency_ms': payload.get('latency_ms'),
                'agent_id': payload.get('agent_id'),
                'ai_label': Config.AI_CONTENT_LABEL,
            }
            yield 'event: meta\ndata: ' + json.dumps(meta, ensure_ascii=False) + '\n\n'
            yield 'event: done\ndata: [DONE]\n\n'
        except GeneratorExit:
            print('⏹️ 客户端断开，停止流式生成')
            return
        except Exception as error:
            import traceback
            print('❌ Stream error:', traceback.format_exc())
            yield 'event: error\ndata: ' + json.dumps({'message': str(error)}, ensure_ascii=False) + '\n\n'

    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })


@auth_bp.route('/code/evaluate', methods=['POST'])
def code_evaluate():
    """代码评测接口（助学场景：复杂度分析 + 问题定位 + 优化建议）。"""
    data = request.get_json(silent=True) or {}
    code = (data.get('prompt') or data.get('code') or '').strip()
    if not code:
        return jsonify({'code': 400, 'message': '请输入代码'}), 400
    try:
        prompt, mode, agent_id, conversation_id, extra_inputs = _build_chat_inputs({
            'prompt': code, 'mode': 'code',
            'conversation_id': data.get('conversation_id'),
        })
        payload = _conversation_turn(prompt, mode='code', agent_id=agent_id or 'tutor',
                                     conversation_id=conversation_id, extra_inputs=extra_inputs)
    except Exception as error:
        import traceback
        print('code_evaluate error:', traceback.format_exc())
        return jsonify({'code': 500, 'message': str(error)}), 500
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'content': payload.get('answer', ''),
        'sources': [item['locator'] for item in (payload.get('citations') or [])],
        'citations': payload.get('citations') or [],
        'trace': payload.get('trace') or [],
        'conversation_id': payload.get('conversation_id'),
        'message_id': payload.get('message_id'),
        'degraded': payload.get('degraded'),
        'ai_generated': True,
        'ai_label': Config.AI_CONTENT_LABEL,
    }}), 200


@auth_bp.route('/diagnosis', methods=['POST'])
def diagnosis():
    """学情诊断接口（助教场景：基于真实作业数据的工作流）。"""
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'code': 400, 'message': '请输入诊断需求'}), 400
    try:
        payload = _conversation_turn(
            prompt, mode='diagnosis', agent_id='class_diagnosis',
            conversation_id=data.get('conversation_id'),
            extra_inputs={'scope': data.get('scope') or '全班',
                          'student_number': data.get('student_number') or '',
                          'focus': prompt},
        )
    except Exception as error:
        import traceback
        print('diagnosis error:', traceback.format_exc())
        return jsonify({'code': 500, 'message': str(error)}), 500
    return jsonify({'code': 200, 'message': 'success', 'data': {
        'content': payload.get('answer', ''),
        'sources': [item['locator'] for item in (payload.get('citations') or [])],
        'citations': payload.get('citations') or [],
        'trace': payload.get('trace') or [],
        'metrics': payload.get('metrics') or {},
        'artifacts': payload.get('artifacts') or [],
        'conversation_id': payload.get('conversation_id'),
        'message_id': payload.get('message_id'),
        'ai_generated': True,
        'ai_label': Config.AI_CONTENT_LABEL,
    }}), 200


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