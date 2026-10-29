r"""为「课程空间」灌入演示数据（仿学习通：课程 / 章节任务点 / 测验 / 签到 / 通知 / 讨论）。

这些是**演示数据**，用于展示教学闭环与统计口径，不是真实学生行为；
对外说明时须标注 demo。

用法：.\.venv\Scripts\python.exe scripts\seed_lms.py [--reset]
"""
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from lms_models import (ChapterProgress, Course, CourseChapter, CourseMember,  # noqa: E402
                        DiscussionPost, DiscussionReply, Notice, NoticeRead, Quiz,
                        QuizAttempt, Signin, SigninRecord)
from models import User  # noqa: E402

COURSE_CODE = 'DS2026'

CHAPTERS = [
    ('第 1 章 绪论与算法复杂度', '算法、时间复杂度、空间复杂度，如何用大 O 描述增长率。',
     ['时间复杂度', '空间复杂度', '渐近分析', '最好/最坏/平均情况'], 25, True),
    ('第 2 章 线性表', '顺序表与链表的存储、插入删除代价、头结点的作用。',
     ['顺序表', '单链表', '双链表', '头结点', '时间复杂度对比'], 30, True),
    ('第 3 章 栈与队列', '栈的 LIFO、队列的 FIFO、循环队列判空判满与假溢出。',
     ['栈', '队列', '循环队列', '假溢出', '队满条件'], 30, True),
    ('第 4 章 串与数组', '模式匹配、KMP 的 next 数组、特殊矩阵压缩存储。',
     ['串', 'KMP', 'next 数组', '压缩存储'], 20, False),
    ('第 5 章 树与二叉树', '二叉树的遍历、性质推导、二叉排序树与堆。',
     ['二叉树性质', '遍历', '二叉排序树', '堆', '平衡'], 40, True),
    ('第 6 章 图', '图的存储、BFS/DFS、最短路径与最小生成树。',
     ['邻接矩阵', '邻接表', 'BFS', 'DFS', 'Dijkstra', 'Floyd'], 45, True),
    ('第 7 章 查找', '顺序查找、折半查找、哈希表与冲突处理、查找长度分析。',
     ['折半查找', '哈希表', '冲突处理', '平均查找长度'], 30, True),
    ('第 8 章 排序', '插入、交换、选择、归并、基数排序，稳定性与复杂度。',
     ['快速排序', '归并排序', '堆排序', '稳定性', '时间复杂度'], 40, True),
]

QUIZ_QUESTIONS = [
    {'stem': '循环队列牺牲一个存储单元的主要目的是什么？',
     'options': ['提高入队速度', '区分队空与队满两种状态', '节省内存空间', '支持优先级出队'],
     'answer': 1,
     'explanation': '牺牲一个单元后，队满条件可写成 (rear+1)%MAXSIZE==front，与队空的 front==rear 区分开。'},
    {'stem': '顺序栈在执行 push 操作时，最先检查的是？',
     'options': ['栈是否为空', '栈顶指针是否越界（栈满）', '元素是否合法', '是否需要扩容到链表'],
     'answer': 1,
     'explanation': '入栈前必须判满，否则会越界写入；出栈前才判空。'},
    {'stem': '已知循环队列 MAXSIZE=50，front=10，rear=45，当前元素个数是？',
     'options': ['35', '34', '36', '45'],
     'answer': 0,
     'explanation': '(rear - front + MAXSIZE) % MAXSIZE = (45-10+50)%50 = 35。'},
    {'stem': '关于队列的「假溢出」，下列说法正确的是？',
     'options': ['数组真的满了', '队头指针后移导致前方空间不可用，rear 已到末尾',
                 '入队操作写错了下标', '队列长度超过 MAXSIZE'],
     'answer': 1,
     'explanation': '假溢出是顺序队列的固有缺陷，靠循环队列（取模回绕）解决。'},
    {'stem': '使用栈判断括号匹配时，遇到右括号但栈为空，说明？',
     'options': ['匹配成功', '右括号多余，表达式不合法', '左括号多余', '需要继续入栈'],
     'answer': 1,
     'explanation': '右括号出现时栈必须非空，否则没有对应的左括号。'},
]


def reset(teacher_id):
    for model in (SigninRecord, Signin, NoticeRead, Notice, QuizAttempt, Quiz,
                  DiscussionReply, DiscussionPost, ChapterProgress, CourseChapter, CourseMember):
        model.query.delete()
    Course.query.delete()
    db.session.commit()


def main():
    reset_requested = '--reset' in sys.argv
    with app.app_context():
        teacher = User.query.filter_by(role='teacher').order_by(User.id).first()
        students = User.query.filter_by(role='student').order_by(User.id).all()
        if not teacher or not students:
            print('缺少演示用户，请先运行 scripts/seed_demo.py')
            return 1

        existing = Course.query.filter_by(code=COURSE_CODE).first()
        if existing and not reset_requested:
            print(f'课程已存在（id={existing.id}），如需重灌请加 --reset')
            return 0
        if existing:
            reset(teacher.id)

        course = Course(title='数据结构与算法', code=COURSE_CODE, term='2026 秋',
                        description='计算机科学与技术专业核心课。以「任务点 + 随堂测验 + 讨论 + 签到」组织教学，'
                                    'AI 导读与 AI 助教均跑在本机模型上，回答带教材引用。',
                        teacher_id=teacher.id, color='#2d8cf0')
        db.session.add(course)
        db.session.flush()

        db.session.add(CourseMember(course_id=course.id, user_id=teacher.id, role='teacher'))
        for student in students:
            db.session.add(CourseMember(course_id=course.id, user_id=student.id, role='student'))

        chapters = []
        for index, (title, summary, points, minutes, is_task) in enumerate(CHAPTERS, start=1):
            chapter = CourseChapter(course_id=course.id, order_index=index, title=title,
                                    summary=summary, key_points=__import__('json').dumps(points, ensure_ascii=False),
                                    est_minutes=minutes, is_task_point=is_task)
            db.session.add(chapter)
            chapters.append(chapter)
        db.session.flush()

        # 学习进度：按学号做确定性伪随机，保证每次重灌结果一致
        task_chapters = [c for c in chapters if c.is_task_point]
        for student in students:
            seed = int(''.join(ch for ch in (student.student_number or '0') if ch.isdigit()) or 0)
            rng = random.Random(seed)
            # 大多数学生完成 5~7 个任务点，少量落后，便于演示预警名单
            done_count = int(rng.triangular(3, len(task_chapters), len(task_chapters)))
            for chapter in task_chapters[:done_count]:
                db.session.add(ChapterProgress(chapter_id=chapter.id, user_id=student.id,
                                               status='done', minutes=chapter.est_minutes or 20))
            if done_count < len(task_chapters) and rng.random() < 0.4:
                db.session.add(ChapterProgress(chapter_id=task_chapters[done_count].id, user_id=student.id,
                                               status='doing', minutes=8))

        db.session.flush()

        # 第 3 章随堂测验（教师预置）+ 学生作答记录
        quiz = Quiz(course_id=course.id, chapter_id=chapters[2].id,
                    title='第 3 章 栈与队列 · 随堂测验（5 题）',
                    questions=__import__('json').dumps(QUIZ_QUESTIONS, ensure_ascii=False),
                    created_by=teacher.id, ai_meta=None)
        db.session.add(quiz)
        db.session.flush()
        for student in students[:40]:
            seed = int(''.join(ch for ch in (student.student_number or '0') if ch.isdigit()) or 0)
            rng = random.Random(seed + 7)
            strength = rng.choice([0.6, 0.8, 0.8, 1.0, 1.0])
            answers, detail, correct = [], [], 0
            for index, question in enumerate(QUIZ_QUESTIONS):
                hit = rng.random() < strength
                chosen = question['answer'] if hit else (question['answer'] + 1) % 4
                answers.append(chosen)
                correct += 1 if hit else 0
                detail.append({'index': index, 'chosen': chosen, 'answer': question['answer'],
                               'correct': hit, 'explanation': question['explanation']})
            db.session.add(QuizAttempt(quiz_id=quiz.id, user_id=student.id,
                                       answers=__import__('json').dumps(answers),
                                       score=round(correct / len(QUIZ_QUESTIONS) * 100),
                                       total=len(QUIZ_QUESTIONS), correct=correct,
                                       detail=__import__('json').dumps(detail, ensure_ascii=False)))

        # 签到：最近 6 次课，缺席比例按学号确定性生成
        signin_titles = ['第 1 次课 · 课程导论', '第 2 次课 · 线性表', '第 3 次课 · 栈与队列',
                         '第 4 次课 · 树与二叉树', '第 5 次课 · 图的最短路径', '第 6 次课 · 查找与哈希']
        for offset, title in enumerate(signin_titles, start=0):
            signin = Signin(course_id=course.id, code=f'{random.randint(1000, 9999)}', title=title,
                            created_by=teacher.id, expire_minutes=10, closed=True,
                            created_at=datetime.now() - timedelta(days=len(signin_titles) - offset))
            db.session.add(signin)
            db.session.flush()
            for student in students:
                seed = int(''.join(ch for ch in (student.student_number or '0') if ch.isdigit()) or 0)
                if random.Random(seed + offset * 13).random() < 0.9:
                    db.session.add(SigninRecord(signin_id=signin.id, user_id=student.id))

        # 通知
        notices = [
            ('本章任务点与测验说明', '第 3 章「栈与队列」任务点包含：教材 3.1–3.3 节 + 循环队列判满推导。'
                                 '完成后请做随堂测验，错题会自动进入错题本。', True),
            ('第 6 章作业提交提醒', '图的最短路径作业本周日 23:59 截止，请提交算法对比与复杂度分析。', False),
            ('期中测验范围', '考试范围：第 1–5 章 + 第 6 章 Dijkstra/Floyd。允许带一张 A4 手写公式纸。', False),
        ]
        for title, content, pinned in notices:
            db.session.add(Notice(course_id=course.id, title=title, content=content,
                                  created_by=teacher.id, pinned=pinned,
                                  created_at=datetime.now() - timedelta(days=random.randint(1, 4))))

        # 讨论区：一条学生提问 + 教师回复
        post = DiscussionPost(course_id=course.id, chapter_id=chapters[2].id, user_id=students[3].id,
                              title='循环队列牺牲一个单元，那实际容量到底是 MAXSIZE 还是 MAXSIZE-1？',
                              content='书上写队满条件是 (rear+1)%MAXSIZE==front，这样的话最多只能存 MAXSIZE-1 个元素，'
                                      '那定义 MAXSIZE=50 时我最开始是不是只能当 49 用？',
                              reply_count=1, created_at=datetime.now() - timedelta(hours=6))
        db.session.add(post)
        db.session.flush()
        db.session.add(DiscussionReply(post_id=post.id, user_id=teacher.id, is_ai=False,
                                       content='是的，牺牲一个单元后有效容量是 MAXSIZE-1。想要存满 50 个可以改用'
                                               '「计数变量 size」或「标志位 tag」两种方案，代价是多维护一个变量。'))

        db.session.commit()
        print('演示数据已写入：')
        print(f'  课程：{course.title}（邀请码 {course.code}，id={course.id}）')
        print(f'  章节：{len(chapters)} 个（任务点 {len(task_chapters)} 个）')
        print(f'  成员：1 位教师 + {len(students)} 名学生')
        print(f'  测验：1 套 / 作答记录 {min(40, len(students))} 条')
        print(f'  签到：{len(signin_titles)} 次（已结束）｜通知：{len(notices)} 条｜讨论：1 帖 1 回')
        print('  注意：以上均为演示数据（demo），不代表真实教学行为。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())