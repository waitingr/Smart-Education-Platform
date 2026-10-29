"""生成演示数据：教师端、55 名学生、作业与提交、平台使用记录、用户反馈。

用途：
  1. 让「学情诊断」「使用看板」「规模化举证」有真实数据可用；
  2. 复现评测/演示环境（不依赖真实教学活动）。

用法：
    python scripts/seed_demo.py            # 已有数据时跳过
    python scripts/seed_demo.py --force    # 清空演示数据后重建
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models import (AgentRun, Conversation, EvaluationRun, Feedback, Homework,  # noqa: E402
                    HomeworkSubmission, KnowledgeDoc, Message, UsageEvent, User, UserProfile)

random.seed(20260101)

TEACHER_PASSWORD = 'Teacher@123'
STUDENT_PASSWORD = 'Student@123'

SURNAMES = '陈林王张刘李赵周吴徐孙马朱胡郭何高罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡'
GIVEN = ['一诺', '子墨', '思远', '雨欣', '浩然', '若曦', '晨曦', '志强', '雅静', '文博',
         '嘉怡', '宇航', '欣怡', '俊杰', '梦琪', '泽宇', '诗涵', '博文', '佳琪', '明轩',
         '芷若', '奕辰', '书瑶', '沐辰', '清越', '知远', '安然', '亦航', '可欣', '梓涵']

HOMEWORKS = [
    ('第3章 栈与队列：括号匹配与表达式求值', '栈与队列', '2026-10-12 23:59', '实现括号匹配校验，并说明循环队列判满为何要牺牲一个存储单元。'),
    ('第5章 二叉树：遍历与性质证明', '树与二叉树', '2026-10-19 23:59', '写出二叉树先序/中序/后序遍历序列，并证明 n₀ = n₂ + 1。'),
    ('第6章 图：最短路径算法对比', '图与最短路径', '2026-10-26 23:59', '对比 Dijkstra、Bellman-Ford、Floyd 的适用条件与复杂度，说明负权边的影响。'),
    ('期中测验：算法复杂度与排序稳定性', '算法复杂度', '2026-11-02 23:59', '分析快排、归并、堆排序的最坏/平均复杂度与稳定性，并说明各自的退化条件。'),
]

AGENTS = [
    ('tutor', 'learn'), ('learning_path', 'learn'), ('mistake_review', 'learn'), ('quiz', 'learn'),
    ('complex_solver', 'learn'), ('lesson_plan', 'teach'), ('grading', 'teach'),
    ('class_diagnosis', 'teach'), ('material_gen', 'teach'), ('frontier', 'research'),
    ('writing', 'research'), ('data_analysis', 'research'),
]

FEEDBACKS = [
    (1, 5, '分步讲解很清楚，引用能直接翻到教材对应章节，复习效率高。'),
    (1, 5, '备课时用智能备课生成的教学设计框架很实用，稍作修改就能上课。'),
    (1, 4, '错题归因指出了我把「不稳定」等同于「错误」的误区，很受用。'),
    (1, 5, '学情诊断直接列出班级薄弱知识点，比手工统计省了一个多小时。'),
    (1, 4, '学习路径按周拆得合理，但希望能自定义每周时长。'),
    (-1, 2, '离线模式下部分答案偏「摘录」，希望接入大模型后更连贯。'),
    (1, 5, '引用出处标注到章节，写论文时可以直接核对原文，这点很好。'),
    (1, 4, '代码评测给的复杂度分析和优化建议比较到位。'),
]


def ensure_user(username, email, role, password, number=None):
    user = User.query.filter_by(email=email).first()
    if user:
        return user, False
    user = User(username=username, email=email, role=role)
    if role == 'teacher':
        user.employee_number = number
    else:
        user.student_number = number
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user, True


def clear_demo():
    """仅清理本脚本可重建的业务数据，保留知识库文档与索引。"""
    for model in (Feedback, UsageEvent, AgentRun, EvaluationRun, Message, Conversation,
                  HomeworkSubmission, Homework, UserProfile):
        model.query.delete()
    User.query.filter(User.email != 'teacher@smartedu.cn').delete()
    User.query.filter_by(email='teacher@smartedu.cn').delete()
    db.session.commit()


def main():
    force = '--force' in sys.argv
    with app.app_context():
        db.create_all()

        existing = User.query.filter_by(email='teacher@smartedu.cn').first()
        if existing and not force:
            print('演示数据已存在（教师账号 teacher@smartedu.cn）。如需重建请加 --force')
            return 0
        if force:
            print('清理旧数据…')
            clear_demo()

        # ---------- 教师 ----------
        teacher, _ = ensure_user('王建国', 'teacher@smartedu.cn', 'teacher', TEACHER_PASSWORD, 'T2026001')
        assistant, _ = ensure_user('李文静', 'assistant@smartedu.cn', 'teacher', TEACHER_PASSWORD, 'T2026002')
        print(f'教师账号：teacher@smartedu.cn / {TEACHER_PASSWORD}')

        # ---------- 学生 55 人 ----------
        students = []
        used_names = set()
        for index in range(1, 56):
            name = None
            for _ in range(60):
                candidate = random.choice(SURNAMES) + random.choice(GIVEN)
                if candidate not in used_names:
                    name = candidate
                    used_names.add(candidate)
                    break
            if name is None:
                name = f'同学{index:02d}'
                used_names.add(name)
            number = f'20230101{index:02d}'
            student, _ = ensure_user(name, f'student{number}@smartedu.cn', 'student', STUDENT_PASSWORD, number)
            students.append(student)
        db.session.commit()
        print(f'学生账号 {len(students)} 个：student2023010101@smartedu.cn / {STUDENT_PASSWORD}')

        # ---------- 学习者画像 ----------
        levels = ['beginner', 'intermediate', 'intermediate', 'advanced']
        styles = ['balanced', 'socratic', 'example', 'concise']
        weak_pool = ['图的最短路径', '二叉树性质', '哈希冲突处理', '时间复杂度分析', '循环队列判满', '排序稳定性', '堆的调整']
        for student in students:
            profile = UserProfile(
                user_id=student.id,
                level=random.choice(levels),
                learning_style=random.choice(styles),
                detail_level=random.choice(['brief', 'standard', 'deep']),
                goal=random.choice(['期末冲刺 85+', '准备考研 408', '补强图算法', '参加蓝桥杯', '跟上课程进度']),
                weak_points=json.dumps(random.sample(weak_pool, 2), ensure_ascii=False),
                interactions=random.randint(3, 60),
            )
            db.session.add(profile)
        db.session.commit()

        # ---------- 作业与提交 ----------
        submissions = 0
        now = datetime.now()
        for offset, (title, point, deadline, description) in enumerate(HOMEWORKS):
            homework = Homework(title=title, description=description, deadline=deadline,
                                created_by=teacher.id, created_at=now - timedelta(days=28 - offset * 7))
            db.session.add(homework)
            db.session.flush()
            # 越靠后的作业完成率略低，制造真实趋势
            submit_ratio = [0.96, 0.93, 0.87, 0.82][offset]
            for student in students:
                if random.random() > submit_ratio:
                    continue
                base = random.gauss(78 - offset * 2, 11)
                if student.student_number in ('2023010107', '2023010112', '2023010131'):
                    base -= random.uniform(8, 16)  # 制造几名需要预警的学生
                score = int(max(28, min(100, round(base)))) if random.random() < 0.9 else None
                submission = HomeworkSubmission(
                    homework_id=homework.id,
                    student_id=student.id,
                    content=f'【{student.username}】{point} 作业作答：思路说明、算法描述与复杂度分析（演示数据）。',
                    score=score,
                    comment='批注：思路正确，注意边界条件的讨论。' if score is not None and score >= 80 else None,
                    submitted_at=now - timedelta(days=random.randint(1, 20), hours=random.randint(0, 20)),
                )
                db.session.add(submission)
                submissions += 1
        db.session.commit()
        print(f'作业 {len(HOMEWORKS)} 次，提交 {submissions} 份')

        # ---------- 使用记录（近 7 天趋势） ----------
        rows = 0
        for day in range(6, -1, -1):
            day_time = now - timedelta(days=day)
            volume = random.randint(14, 26) + (7 - day) * 4
            for _ in range(volume):
                agent_id, scenario = random.choice(AGENTS)
                role = random.choice(['student', 'student', 'student', 'teacher'])
                user = teacher if role == 'teacher' else random.choice(students)
                db.session.add(UsageEvent(
                    user_id=user.id,
                    event_type='agent_run',
                    agent_id=agent_id,
                    scenario=scenario,
                    latency_ms=random.randint(120, 2600),
                    degraded=random.random() < 0.65,
                    success=random.random() > 0.02,
                    role=role,
                    detail=json.dumps({'source': 'seed', 'step': 'demo'}, ensure_ascii=False),
                    created_at=day_time.replace(hour=random.randint(8, 21), minute=random.randint(0, 59)),
                ))
                rows += 1
        db.session.commit()
        print(f'使用记录 {rows} 条（覆盖近 7 天）')

        # ---------- 用户反馈 ----------
        for rating, stars, comment in FEEDBACKS:
            agent_id, scenario = random.choice(AGENTS)
            db.session.add(Feedback(
                user_id=random.choice(students + [teacher]).id,
                agent_id=agent_id,
                scenario=scenario,
                rating=rating,
                usefulness=stars,
                comment=comment,
                created_at=now - timedelta(days=random.randint(0, 6), hours=random.randint(0, 20)),
            ))
        db.session.commit()
        print(f'用户反馈 {len(FEEDBACKS)} 条')

        # ---------- 知识库登记表与实际文档对齐 ----------
        from kb_service import get_kb
        kb = get_kb()
        registered = 0
        for item in kb.list_documents():
            record = KnowledgeDoc.query.filter_by(doc_id=item.get('doc_id')).first()
            if not record:
                record = KnowledgeDoc(doc_id=item.get('doc_id'))
                db.session.add(record)
            record.title = item.get('title') or item.get('doc_title') or '未命名文档'
            record.source_type = item.get('source_type') or 'other'
            record.authors = item.get('authors')
            record.year = item.get('year')
            record.publisher = item.get('publisher')
            record.authority = item.get('authority') or 3
            record.chunk_count = item.get('chunk_count') or 0
            record.uploaded_by = teacher.id
            registered += 1
        db.session.commit()
        print(f'知识库文档登记 {registered} 篇')

        print('\n演示数据准备完成。')
        print(f'  教师：teacher@smartedu.cn / {TEACHER_PASSWORD}')
        print(f'  学生：student2023010101@smartedu.cn / {STUDENT_PASSWORD}')
        return 0


if __name__ == '__main__':
    sys.exit(main())