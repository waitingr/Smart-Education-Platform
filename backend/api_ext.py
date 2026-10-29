"""扩展 API：智能体工作流、多轮会话、知识库管理、个性化画像、反馈、数据分析与评测。

统一响应结构：{code, message, data}
所有 AI 输出都带 ai_generated / ai_label 字段，供前端展示「AI 生成内容」标识（合规要求）。
"""
import json
import os
import re
import tempfile
import time
import uuid

from flask import Blueprint, jsonify, request

from agents import AGENTS, agent_catalog, check_academic_integrity, run_agent
from auth_utils import get_current_user_from_request, login_required, optional_user, teacher_required
from config import Config
from extensions import db
from kb_service import SOURCE_TYPES, get_kb, rebuild_kb
from llm_client import get_llm
from models import (AgentRun, Conversation, EvaluationRun, Feedback, KnowledgeDoc, Message,
                    UserProfile, User)
from telemetry import overview, record_agent_run, record_event

ext_bp = Blueprint('ext', __name__)

HISTORY_LIMIT = 10


# ==================== 公共工具 ====================
def _ok(data=None, message='success'):
    return jsonify({'code': 200, 'message': message, 'data': data}), 200


def _fail(message, code=400):
    return jsonify({'code': code, 'message': message}), code


def _profile_dict(user):
    if not user:
        return {'level': 'intermediate', 'goal': '', 'learning_style': 'balanced',
                'detail_level': 'standard', 'weak_points': [], 'interactions': 0}
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id, level='intermediate', goal='', learning_style='balanced',
                              detail_level='standard', weak_points='[]')
        db.session.add(profile)
        db.session.commit()
    data = profile.to_dict()
    data['username'] = user.username
    data['role'] = user.role
    return data


def _conversation_history(conversation_id, limit=HISTORY_LIMIT):
    if not conversation_id:
        return []
    messages = (Message.query.filter_by(conversation_id=conversation_id)
                .order_by(Message.id.desc()).limit(limit).all())
    return [{'role': item.role, 'content': item.content} for item in reversed(messages)]


def _get_or_create_conversation(user, conversation_id, agent_id, title_hint):
    conversation = None
    if conversation_id:
        try:
            conversation = Conversation.query.get(int(conversation_id))
        except (TypeError, ValueError):
            conversation = None
        if conversation and user and conversation.user_id not in (None, user.id):
            conversation = None
    if conversation is None:
        scenario = AGENTS.get(agent_id, {}).get('scenario', 'learn')
        conversation = Conversation(
            user_id=getattr(user, 'id', None),
            session_id=request.headers.get('X-Session-Id'),
            title=(title_hint or '新的对话')[:80],
            agent_id=agent_id,
            scenario=scenario,
        )
        db.session.add(conversation)
        db.session.commit()
    return conversation


def _safe_filename(filename):
    name = os.path.basename(filename or '').replace('\x00', '')
    name = re.sub(r'[<>:"/\\|?*]', '_', name).strip('. ')
    return name or f'upload_{uuid.uuid4().hex[:8]}'


def _validate_inputs(agent, inputs):
    missing = []
    for spec in agent['inputs']:
        if spec.get('required') and not str(inputs.get(spec['key'], '') or '').strip():
            missing.append(spec['label'])
    return missing


# ==================== 智能体 ====================
@ext_bp.route('/agents', methods=['GET'])
def get_agents():
    return _ok({
        'scenarios': agent_catalog(),
        'llm': get_llm().health(),
        'discipline': {
            'name': Config.DISCIPLINE_NAME,
            'course': Config.DISCIPLINE_COURSE,
            'platform': Config.PLATFORM_NAME,
        },
        'ai_label': Config.AI_CONTENT_LABEL,
    })


@ext_bp.route('/agents/<agent_id>/run', methods=['POST'])
@optional_user
def run_agent_endpoint(agent_id):
    if agent_id not in AGENTS:
        return _fail(f'未知智能体：{agent_id}', 404)
    agent = AGENTS[agent_id]
    body = request.get_json(silent=True) or {}
    inputs = dict(body.get('inputs') or {})
    for key, value in body.items():
        if key not in ('inputs', 'conversation_id'):
            inputs.setdefault(key, value)

    missing = _validate_inputs(agent, inputs)
    if missing:
        return _fail('缺少必填内容：' + '、'.join(missing))

    # 学术诚信前置拦截（写作、批改、答疑场景）
    guard_text = ' '.join(str(inputs.get(key, '')) for key in ('question', 'topic', 'draft', 'student_answer'))
    guard = check_academic_integrity(guard_text)
    if guard['blocked'] and agent_id not in ('grading', 'writing'):
        return _fail(f"请求涉及学术诚信风险（{guard['reason']}），已拒绝生成。如需合规支持，请调整提问方式。", 403)

    user = request.current_user
    profile = _profile_dict(user)
    conversation = _get_or_create_conversation(
        user, body.get('conversation_id'), agent_id,
        inputs.get('topic') or inputs.get('question') or agent['name'],
    )
    history = _conversation_history(conversation.id)
    started = time.time()
    try:
        payload = run_agent(agent_id, inputs, user=user, profile=profile, history=history,
                            session_id=request.headers.get('X-Session-Id'))
    except Exception as error:
        import traceback
        print(f'[agents] {agent_id} 执行失败：{traceback.format_exc()}')
        record_event(user, 'agent_error', agent_id=agent_id, scenario=agent['scenario'], success=False,
                     detail=str(error))
        return _fail(f'智能体执行失败：{error}', 500)

    # 落库：会话消息 + 运行记录 + 使用行为
    user_message = Message(
        conversation_id=conversation.id, role='user', agent_id=agent_id,
        content=inputs.get('question') or inputs.get('topic') or agent['name'],
    )
    ai_message = Message(
        conversation_id=conversation.id, role='assistant', agent_id=agent_id,
        content=payload.get('answer', ''),
        citations=json.dumps(payload.get('citations') or [], ensure_ascii=False),
        meta=json.dumps({
            'trace': payload.get('trace'),
            'provider': payload.get('provider'),
            'degraded': payload.get('degraded'),
            'artifacts': payload.get('artifacts'),
            'facts': payload.get('facts'),
            'agent_name': payload.get('agent_name'),
            'latency_ms': payload.get('latency_ms'),
            'ai_label': Config.AI_CONTENT_LABEL,
        }, ensure_ascii=False),
        ai_generated=True,
    )
    db.session.add_all([user_message, ai_message])
    conversation.message_count = (conversation.message_count or 0) + 2
    conversation.updated_at = db.func.now()
    db.session.commit()

    run_id = record_agent_run(user, payload, inputs)
    record_event(user, 'agent_run', agent_id=agent_id, scenario=agent['scenario'],
                 latency_ms=payload.get('latency_ms'), degraded=payload.get('degraded'),
                 detail={'citation_count': len(payload.get('citations') or [])})
    if user:
        profile_row = UserProfile.query.filter_by(user_id=user.id).first()
        if profile_row:
            profile_row.interactions = (profile_row.interactions or 0) + 1
            db.session.commit()

    payload.update({
        'conversation_id': conversation.id,
        'message_id': ai_message.id,
        'run_id': run_id,
        'ai_generated': True,
        'ai_label': Config.AI_CONTENT_LABEL,
    })
    return _ok(payload)


# ==================== 多轮会话 ====================
@ext_bp.route('/conversations', methods=['GET'])
@optional_user
def list_conversations():
    user = request.current_user
    query = Conversation.query
    if user:
        query = query.filter_by(user_id=user.id)
    else:
        query = query.filter_by(user_id=None, session_id=request.headers.get('X-Session-Id'))
    items = query.order_by(Conversation.updated_at.desc()).limit(50).all()
    return _ok({'conversations': [item.to_dict() for item in items], 'count': len(items)})


@ext_bp.route('/conversations/<int:conversation_id>', methods=['GET'])
@optional_user
def get_conversation(conversation_id):
    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        return _fail('会话不存在', 404)
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.id.asc()).all()
    return _ok({**conversation.to_dict(), 'messages': [item.to_dict() for item in messages]})


@ext_bp.route('/conversations/<int:conversation_id>', methods=['DELETE'])
@optional_user
def delete_conversation(conversation_id):
    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        return _fail('会话不存在', 404)
    user = request.current_user
    if user and conversation.user_id not in (None, user.id):
        return _fail('无权删除该会话', 403)
    Message.query.filter_by(conversation_id=conversation_id).delete()
    db.session.delete(conversation)
    db.session.commit()
    return _ok(None, '会话已删除')


# ==================== 个性化画像 ====================
@ext_bp.route('/profile', methods=['GET'])
@optional_user
def get_profile():
    user = request.current_user
    if not user:
        return _ok({'level': 'intermediate', 'goal': '', 'learning_style': 'balanced',
                    'detail_level': 'standard', 'weak_points': [], 'interactions': 0, 'guest': True})
    return _ok(_profile_dict(user))


@ext_bp.route('/profile', methods=['PATCH'])
@login_required
def update_profile():
    user = request.current_user
    body = request.get_json(silent=True) or {}
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id, weak_points='[]')
        db.session.add(profile)
    for field in ('level', 'goal', 'learning_style', 'detail_level'):
        if field in body and body[field] is not None:
            setattr(profile, field, str(body[field])[:200])
    if 'weak_points' in body:
        weak = body['weak_points']
        if isinstance(weak, str):
            weak = [item.strip() for item in re.split(r'[,，;；\s]+', weak) if item.strip()]
        profile.weak_points = json.dumps(list(weak)[:12], ensure_ascii=False)
    db.session.commit()
    record_event(user, 'profile_update', detail={'level': profile.level})
    return _ok(_profile_dict(user), '画像已更新')


@ext_bp.route('/profile/weak-points', methods=['POST'])
@login_required
def add_weak_point():
    """由错题归因/自测结果回写薄弱点，形成「使用越久越懂你」的自适应闭环。"""
    user = request.current_user
    body = request.get_json(silent=True) or {}
    points = body.get('points') or []
    if isinstance(points, str):
        points = [points]
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id, weak_points='[]')
        db.session.add(profile)
    weak = profile.weak_list()
    for point in points:
        point = str(point).strip()[:40]
        if point and point not in weak:
            weak.append(point)
    profile.weak_points = json.dumps(weak[:12], ensure_ascii=False)
    db.session.commit()
    return _ok({'weak_points': weak[:12]}, '薄弱点已记录')


# ==================== 反馈（用户认可度） ====================
@ext_bp.route('/feedback', methods=['POST'])
@optional_user
def create_feedback():
    user = request.current_user
    body = request.get_json(silent=True) or {}
    rating = int(body.get('rating') or 0)
    usefulness = int(body.get('usefulness') or 0)
    if rating not in (-1, 0, 1):
        return _fail('rating 取值应为 -1 / 0 / 1')
    feedback = Feedback(
        user_id=getattr(user, 'id', None),
        conversation_id=body.get('conversation_id'),
        message_id=body.get('message_id'),
        agent_id=body.get('agent_id'),
        scenario=body.get('scenario') or AGENTS.get(body.get('agent_id'), {}).get('scenario'),
        rating=rating,
        usefulness=max(0, min(usefulness, 5)),
        comment=(body.get('comment') or '')[:1000],
    )
    db.session.add(feedback)
    db.session.commit()
    record_event(user, 'feedback', agent_id=feedback.agent_id, scenario=feedback.scenario,
                 detail={'rating': rating, 'usefulness': usefulness})

    # 负反馈回写薄弱点：实现「根据使用行为自适应」
    if user and rating < 0 and body.get('weak_point'):
        profile = UserProfile.query.filter_by(user_id=user.id).first()
        if profile:
            weak = profile.weak_list()
            point = str(body['weak_point'])[:40]
            if point and point not in weak:
                weak.append(point)
                profile.weak_points = json.dumps(weak[:12], ensure_ascii=False)
                db.session.commit()
    return _ok(feedback.to_dict(), '感谢反馈')


@ext_bp.route('/feedback', methods=['GET'])
@teacher_required
def list_feedback():
    items = Feedback.query.order_by(Feedback.created_at.desc()).limit(100).all()
    return _ok([item.to_dict() for item in items])


@ext_bp.route('/analytics/overview', methods=['GET'])
@optional_user
def analytics_overview():
    days = request.args.get('days', type=int) or 7
    data = overview(days=days)
    data['knowledge_base'] = get_kb().stats() if True else {}
    data['llm'] = get_llm().health()
    return _ok(data)


# ==================== 知识库管理 ====================
@ext_bp.route('/kb/stats', methods=['GET'])
def kb_stats():
    return _ok({
        'stats': get_kb().stats(),
        'source_types': [{'value': key, 'label': value} for key, value in SOURCE_TYPES.items()],
    })


@ext_bp.route('/kb/docs', methods=['GET'])
def kb_docs():
    documents = get_kb().list_documents()
    return _ok({'documents': documents, 'count': len(documents)})


@ext_bp.route('/kb/search', methods=['POST'])
def kb_search():
    body = request.get_json(silent=True) or {}
    query = (body.get('query') or '').strip()
    if not query:
        return _fail('请输入检索内容')
    top_k = min(int(body.get('top_k') or Config.KB_TOP_K), 20)
    results = get_kb().search(query, top_k=top_k)
    return _ok({'query': query, 'results': results, 'count': len(results)})


@ext_bp.route('/kb/upload', methods=['POST'])
@teacher_required
def kb_upload():
    if 'file' not in request.files:
        return _fail('未选择文件')
    uploaded = request.files['file']
    if not uploaded.filename:
        return _fail('文件名为空')
    extension = os.path.splitext(uploaded.filename)[1].lower()
    allowed = {'.txt', '.md', '.pdf', '.docx', '.xlsx', '.csv', '.tsv'}
    if extension not in allowed:
        return _fail('知识库仅支持 txt / md / pdf / docx / xlsx / csv 文件')

    filename = _safe_filename(uploaded.filename)
    temp_path = os.path.join(tempfile.gettempdir(), f'kb_{uuid.uuid4().hex}{extension}')
    uploaded.save(temp_path)
    title = (request.form.get('title') or os.path.splitext(filename)[0]).strip()
    source_type = (request.form.get('source_type') or 'upload').strip()
    if source_type not in SOURCE_TYPES:
        source_type = 'upload'
    meta = {
        'authors': (request.form.get('authors') or '').strip(),
        'year': (request.form.get('year') or '').strip(),
        'publisher': (request.form.get('publisher') or '').strip(),
        'authority': request.form.get('authority') or '3',
    }
    try:
        document = get_kb().add_document(temp_path, title=title, source_type=source_type, meta=meta)
    except ValueError as error:
        return _fail(str(error))
    except Exception as error:
        return _fail(f'入库失败：{error}', 500)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    record = KnowledgeDoc(
        doc_id=document.get('doc_id'),
        title=title,
        source_type=source_type,
        authors=meta['authors'],
        year=meta['year'],
        publisher=meta['publisher'],
        authority=int(meta['authority']) if str(meta['authority']).isdigit() else 3,
        filename=filename,
        chunk_count=document.get('chunks') or 0,
        uploaded_by=request.current_user.id,
    )
    db.session.add(record)
    db.session.commit()
    record_event(request.current_user, 'kb_upload', detail={'title': title, 'chunks': record.chunk_count})
    return _ok({'document': document, 'record': record.to_dict(), 'stats': get_kb().stats()}, '已入库并生效')


@ext_bp.route('/kb/docs/<doc_id>', methods=['DELETE'])
@teacher_required
def kb_delete(doc_id):
    if not get_kb().delete_document(doc_id):
        return _fail('文档不存在或删除失败', 404)
    KnowledgeDoc.query.filter_by(doc_id=doc_id).delete()
    db.session.commit()
    return _ok({'stats': get_kb().stats()}, '文档已移出知识库')


@ext_bp.route('/kb/rebuild', methods=['POST'])
@teacher_required
def kb_rebuild():
    started = time.time()
    meta = rebuild_kb()
    return _ok({'stats': get_kb().stats(), 'seconds': round(time.time() - started, 2)}, '索引重建完成')


# ==================== 评测（内容质量度的量化支撑） ====================
def _load_testset():
    path = os.path.join(Config.EVAL_DIR, 'testset.jsonl')
    if not os.path.isfile(path):
        return []
    cases = []
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                cases.append(json.loads(line))
            except ValueError:
                continue
    return cases


@ext_bp.route('/eval/cases', methods=['GET'])
def eval_cases():
    cases = _load_testset()
    return _ok({'count': len(cases), 'cases': cases})


@ext_bp.route('/eval/run', methods=['POST'])
@optional_user
def eval_run():
    body = request.get_json(silent=True) or {}
    limit = min(int(body.get('limit') or 0) or 12, 50)
    cases = _load_testset()[:limit]
    if not cases:
        return _fail('评测集为空，请检查 data/eval/testset.jsonl')
    if get_llm().provider == 'offline' and not body.get('allow_offline'):
        # 离线模式仍可跑通链路，但在报告中明确标注，避免误读为模型效果
        pass

    results = []
    started = time.time()
    for case in cases:
        payload = run_agent('tutor', {'question': case.get('question', '')}, profile={'level': 'intermediate'})
        answer = payload.get('answer', '')
        keywords_expected = [str(item) for item in (case.get('expect_keywords') or [])]
        hits = [word for word in keywords_expected if word.lower() in answer.lower()]
        recall = round(len(hits) / len(keywords_expected), 3) if keywords_expected else 1.0
        citation_hit = bool(re.search(r'\[\d{1,2}\]', answer)) and bool(payload.get('citations'))
        reference = case.get('reference') or ''
        overlap = round(__import__('text_utils').overlap_score(reference, answer), 3) if reference else None
        passed = recall >= float(case.get('min_recall', 0.6)) and citation_hit
        results.append({
            'id': case.get('id'),
            'question': case.get('question'),
            'passed': passed,
            'keyword_recall': recall,
            'missing_keywords': [word for word in keywords_expected if word not in hits],
            'citation_hit': citation_hit,
            'citation_count': len(payload.get('citations') or []),
            'reference_overlap': overlap,
            'latency_ms': payload.get('latency_ms'),
            'degraded': payload.get('degraded'),
            'answer_preview': (answer or '')[:300],
        })

    total = len(results)
    passed = sum(1 for item in results if item['passed'])
    metrics = {
        'accuracy': round(passed / total, 4) if total else 0,
        'avg_keyword_recall': round(sum(item['keyword_recall'] for item in results) / total, 4) if total else 0,
        'citation_coverage': round(sum(1 for item in results if item['citation_hit']) / total, 4) if total else 0,
        'avg_latency_ms': round(sum(item['latency_ms'] or 0 for item in results) / total, 1) if total else 0,
        'degraded': all(item['degraded'] for item in results) if results else True,
        'provider': get_llm().provider,
        'seconds': round(time.time() - started, 2),
    }

    os.makedirs(os.path.join(Config.EVAL_DIR, 'reports'), exist_ok=True)
    report_name = f"eval_{time.strftime('%Y%m%d_%H%M%S')}.md"
    report_path = os.path.join(Config.EVAL_DIR, 'reports', report_name)
    with open(report_path, 'w', encoding='utf-8') as handle:
        handle.write('# 学科垂类模型输出质量评测报告\n\n')
        handle.write(f"- 评测时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.write(f"- 推理后端：{metrics['provider']}（{'离线演示模式' if metrics['degraded'] else '已接入大模型'}）\n")
        handle.write(f"- 样本数：{total}\n")
        handle.write(f"- 综合准确率：{metrics['accuracy'] * 100:.1f}%\n")
        handle.write(f"- 引用覆盖率：{metrics['citation_coverage'] * 100:.1f}%\n")
        handle.write(f"- 关键词平均召回：{metrics['avg_keyword_recall'] * 100:.1f}%\n\n")
        handle.write('| # | 问题 | 结果 | 关键词召回 | 引用数 | 耗时(ms) |\n| --- | --- | --- | --- | --- | --- |\n')
        for index, item in enumerate(results, start=1):
            handle.write(f"| {index} | {item['question'][:40]} | {'通过' if item['passed'] else '未通过'} | "
                         f"{item['keyword_recall'] * 100:.0f}% | {item['citation_count']} | {item['latency_ms']} |\n")
        handle.write('\n> 判定口径：关键词召回 ≥ 60% 且回答包含有效引用编号即视为通过。\n')

    run = EvaluationRun(
        name=body.get('name') or 'standard',
        dataset='data/eval/testset.jsonl',
        total=total,
        passed=passed,
        metrics=json.dumps(metrics, ensure_ascii=False),
        report_path=os.path.relpath(report_path, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        created_by=getattr(request.current_user, 'id', None),
    )
    db.session.add(run)
    db.session.commit()
    record_event(request.current_user, 'eval_run', detail={'accuracy': metrics['accuracy'], 'total': total})
    return _ok({'metrics': metrics, 'results': results, 'report_path': run.report_path, 'run': run.to_dict()})


@ext_bp.route('/eval/history', methods=['GET'])
def eval_history():
    runs = EvaluationRun.query.order_by(EvaluationRun.created_at.desc()).limit(20).all()
    return _ok([item.to_dict() for item in runs])


# ==================== 合规与说明 ====================
@ext_bp.route('/compliance/statement', methods=['GET'])
def compliance_statement():
    return _ok({
        'ai_label': Config.AI_CONTENT_LABEL,
        'commitments': [
            '不使用未经脱敏的真实个人数据；平台内学生信息仅用于教学管理，导出时自动脱敏学号。',
            '不生成伪造的学术数据、虚假文献或违反科研诚信的内容，学术诚信守卫会拦截此类请求。',
            '所有 AI 输出均带「AI 生成内容」标识，并可回溯到知识库原文出处。',
            '知识库仅收录公开出版的教材、标准、公开论文与教师自有资料。',
        ],
        'data_policy': '默认使用本地部署模型与本地知识库，数据不出校内网；如接入云侧大模型，仅传输脱敏后的问答文本。',
        'traceability': '每条 AI 回答附带 [n] 引用编号，可定位到「文档 · 章节 · 页码」，支持人工复核。',
    })


@ext_bp.route('/health/stack', methods=['GET'])
def health_stack():
    vision = {}
    try:
        from vision_service import get_vision
        vision = get_vision().health()
    except Exception as error:  # 视觉链路不可用不应影响健康检查
        vision = {'available': False, 'notice': f'{type(error).__name__}: {error}'}
    return _ok({
        'llm': get_llm().health(),
        'vision': vision,
        'knowledge_base': get_kb().stats(),
        'discipline': Config.DISCIPLINE_NAME,
        'course': Config.DISCIPLINE_COURSE,
    })