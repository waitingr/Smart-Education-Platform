"""使用行为与反馈埋点（效果验证报告的数据来源）。

对应赛题「用户认可度」「可复制与可推广」：记录真实使用频次、耗时、满意度，
并在 /analytics/overview 中按时间窗口聚合，可直接导出为《06—效果验证报告》的数据部分。
"""
import json
from datetime import datetime, timedelta

from extensions import db
from models import (AgentRun, Conversation, EvaluationRun, Feedback, Homework,
                    HomeworkSubmission, Message, UsageEvent, User)


def _agent_names():
    """智能体 id → 中文名，用于看板展示。"""
    try:
        from agents import list_agents
        names = {}
        for group in list_agents():
            for agent in group.get('agents', []):
                names[agent['id']] = agent.get('name') or agent['id']
        return names
    except Exception:
        return {}


def record_event(user, event_type, agent_id=None, scenario=None, latency_ms=0,
                 degraded=False, success=True, detail=None):
    try:
        event = UsageEvent(
            user_id=getattr(user, 'id', None),
            event_type=event_type,
            agent_id=agent_id,
            scenario=scenario,
            latency_ms=int(latency_ms or 0),
            degraded=bool(degraded),
            success=bool(success),
            role=getattr(user, 'role', None),
            detail=(json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else detail),
        )
        db.session.add(event)
        db.session.commit()
    except Exception as error:  # 埋点失败绝不能影响主流程
        db.session.rollback()
        print(f'[telemetry] 事件写入失败：{error}')


def record_agent_run(user, payload, inputs=None):
    try:
        run = AgentRun(
            user_id=getattr(user, 'id', None),
            agent_id=payload.get('agent_id') or 'unknown',
            scenario=payload.get('scenario'),
            inputs=json.dumps(inputs or {}, ensure_ascii=False),
            answer=payload.get('answer'),
            trace=json.dumps(payload.get('trace') or [], ensure_ascii=False),
            citations=json.dumps(payload.get('citations') or [], ensure_ascii=False),
            latency_ms=int(payload.get('latency_ms') or 0),
            degraded=bool(payload.get('degraded')),
        )
        db.session.add(run)
        db.session.commit()
        return run.id
    except Exception as error:
        db.session.rollback()
        print(f'[telemetry] 智能体运行记录写入失败：{error}')
        return None


def _percent(numerator, denominator, digits=1):
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, digits)


def overview(days=7):
    since = datetime.now() - timedelta(days=max(1, days))
    events = UsageEvent.query.filter(UsageEvent.created_at >= since).all()
    feedbacks = Feedback.query.filter(Feedback.created_at >= since).all()
    runs = AgentRun.query.filter(AgentRun.created_at >= since).all()

    total_events = UsageEvent.query.count()
    total_users = User.query.count()
    student_users = User.query.filter_by(role='student').count()
    teacher_users = User.query.filter(User.role.in_(('teacher', 'admin'))).count()
    total_conversations = Conversation.query.count()
    total_messages = Message.query.count()
    total_runs = AgentRun.query.count()

    active_user_ids = {event.user_id for event in events if event.user_id}
    satisfied = sum(1 for item in feedbacks if (item.rating or 0) > 0)
    dissatisfied = sum(1 for item in feedbacks if (item.rating or 0) < 0)
    comments = [item.comment for item in feedbacks if (item.comment or '').strip()]

    by_agent = {}
    for run in runs:
        bucket = by_agent.setdefault(run.agent_id, {'runs': 0, 'latency': 0, 'degraded': 0})
        bucket['runs'] += 1
        bucket['latency'] += run.latency_ms or 0
        bucket['degraded'] += 1 if run.degraded else 0

    trend = []
    for offset in range(days - 1, -1, -1):
        day = (datetime.now() - timedelta(days=offset)).date()
        day_events = [event for event in events if event.created_at and event.created_at.date() == day]
        run_count = len([item for item in day_events if item.event_type in ('agent_run', 'chat')])
        trend.append({
            'date': day.strftime('%Y-%m-%d'),
            'label': day.strftime('%m-%d'),
            'runs': run_count,
            'count': run_count,
            'users': len({item.user_id for item in day_events if item.user_id}),
        })

    # 真实用户试用反馈（评委要求：至少 2 名真实目标用户的使用证据）
    trial_users = (
        db.session.query(Feedback.user_id, db.func.count(Feedback.id))
        .filter(Feedback.user_id.isnot(None))
        .group_by(Feedback.user_id)
        .all()
    )
    trial_details = []
    for user_id, count in trial_users:
        user = User.query.get(user_id)
        if not user:
            continue
        trial_details.append({
            'user': user.username,
            'role': user.role,
            'student_number': user.student_number,
            'employee_number': user.employee_number,
            'feedback_count': count,
            'first_seen': min(
                (item.created_at for item in feedbacks if item.user_id == user_id),
                default=None,
            ).strftime('%Y-%m-%d') if any(item.user_id == user_id for item in feedbacks) else '',
        })

    latest_eval = EvaluationRun.query.order_by(EvaluationRun.created_at.desc()).first()

    agent_names = _agent_names()
    feedback_items = [
        {
            'rating': item.rating or 0,
            'usefulness': item.usefulness or 0,
            'comment': item.comment or '',
            'agent_id': item.agent_id or '',
            'agent_name': agent_names.get(item.agent_id, item.agent_id or ''),
            'created_at': item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else '',
        }
        for item in sorted(feedbacks, key=lambda row: row.created_at or datetime.min, reverse=True)
    ]

    return {
        'window_days': days,
        'users': {
            'total': total_users,
            'students': student_users,
            'teachers': teacher_users,
            'active': len(active_user_ids),
        },
        'usage': {
            'events_total': total_events,
            'events_window': len(events),
            'conversations': total_conversations,
            'messages': total_messages,
            'agent_runs': total_runs,
            'avg_latency_ms': round(sum(item.latency_ms or 0 for item in runs) / len(runs), 1) if runs else 0,
        },
        'quality': {
            'feedback_count': len(feedbacks),
            'satisfaction_rate': _percent(satisfied, satisfied + dissatisfied) if (satisfied + dissatisfied) else None,
            'positive_rate': _percent(satisfied, satisfied + dissatisfied) if (satisfied + dissatisfied) else None,
            'positive': satisfied,
            'negative': dissatisfied,
            'comments': comments[-10:],
            'citation_coverage': _percent(
                sum(1 for run in runs if run.citations and run.citations not in ('[]', '')),
                len(runs),
            ) if runs else 0,
        },
        'agents': [
            {
                'agent_id': agent_id,
                'name': agent_names.get(agent_id, agent_id),
                'runs': value['runs'],
                'count': value['runs'],
                'avg_latency_ms': round(value['latency'] / value['runs'], 1) if value['runs'] else 0,
                'degraded_rate': _percent(value['degraded'], value['runs']),
            }
            for agent_id, value in sorted(by_agent.items(), key=lambda item: item[1]['runs'], reverse=True)
        ],
        'trend': trend,
        'trial_users': trial_details,
        'feedback': feedback_items,
        'scale': {
            'student_users': student_users,
            'students': student_users,
            'teachers': teacher_users,
            'homeworks': Homework.query.count(),
            'submissions': HomeworkSubmission.query.count(),
            'trial_users': len(trial_details),
            'reaches_50_users': student_users >= 50,
            'note': f'当前覆盖 {student_users} 名学生' + ('（已达 50 人规模化使用口径）' if student_users >= 50 else '（满 50 人后可申报规模化加分）'),
        },
        'latest_evaluation': latest_eval.to_dict() if latest_eval else None,
    }