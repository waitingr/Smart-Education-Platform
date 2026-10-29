"""生成《灵眸智教—作品方案》汇报 PPT（16:9，可编辑文本/表格/图表）。

用法（使用 DSH 自带 Python，内含 python-pptx）：
    python docs/build_pptx.py
输出：
    docs/灵眸智教-作品方案.pptx
"""
import os
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '灵眸智教-作品方案.pptx')

# ---------------- 设计系统 ----------------
INK = RGBColor(0x0F, 0x17, 0x2A)
SLATE = RGBColor(0x47, 0x55, 0x69)
MUTED = RGBColor(0x64, 0x74, 0x8B)
TEAL = RGBColor(0x0E, 0x74, 0x90)
TEAL_LIGHT = RGBColor(0xE0, 0xF2, 0xF7)
AMBER = RGBColor(0xB4, 0x53, 0x09)
AMBER_LIGHT = RGBColor(0xFE, 0xF3, 0xC7)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BG = RGBColor(0xF8, 0xFA, 0xFC)
LINE = RGBColor(0xE2, 0xE8, 0xF0)
GREEN = RGBColor(0x04, 0x78, 0x57)

FONT = '微软雅黑'
W, H = 13.333, 7.5

prs = __import__('pptx').Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
BLANK = prs.slide_layouts[6]


def set_font(run, size=14, bold=False, color=SLATE, name=FONT):
    """同时设置西文与中文字体，避免 PowerPoint 回退成宋体。"""
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = name
    rpr = run._r.get_or_add_rPr()
    for tag in ('a:latin', 'a:ea', 'a:cs'):
        node = rpr.find('{http://schemas.openxmlformats.org/drawingml/2006/main}' + tag.split(':')[1])
        if node is None:
            from lxml import etree
            node = etree.SubElement(rpr, '{http://schemas.openxmlformats.org/drawingml/2006/main}' + tag.split(':')[1])
        node.set('typeface', name)


def textbox(slide, x, y, w, h, text, size=14, bold=False, color=SLATE,
            align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.15):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    lines = text.split('\n')
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.line_spacing = spacing
        run = paragraph.add_run()
        run.text = line
        set_font(run, size, bold, color)
    return box


def rect(slide, x, y, w, h, fill=WHITE, line=None, radius=0.04, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    box = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            box.adjustments[0] = radius
        except Exception:
            pass
    if fill is None:
        box.fill.background()
    else:
        box.fill.solid()
        box.fill.fore_color.rgb = fill
    if line is None:
        box.line.fill.background()
    else:
        box.line.color.rgb = line
        box.line.width = Pt(1)
    box.shadow.inherit = False
    box.text_frame.text = ''
    return box


def new_slide(kicker, title, subtitle=None):
    slide = prs.slides.add_slide(BLANK)
    rect(slide, 0, 0, W, H, fill=BG, shape=MSO_SHAPE.RECTANGLE)
    # 左侧强调条
    rect(slide, 0.62, 0.62, 0.08, 0.62, fill=TEAL, shape=MSO_SHAPE.RECTANGLE)
    textbox(slide, 0.86, 0.58, 8, 0.3, kicker, 11, True, TEAL)
    textbox(slide, 0.86, 0.86, 11.6, 0.62, title, 27, True, INK)
    if subtitle:
        textbox(slide, 0.86, 1.52, 11.6, 0.36, subtitle, 12.5, False, MUTED)
    # 页脚
    rect(slide, 0.62, 6.95, 12.1, 0.01, fill=LINE, shape=MSO_SHAPE.RECTANGLE)
    textbox(slide, 0.62, 7.02, 8, 0.28, '灵眸智教 · 学科垂类大模型与创新应用平台', 9.5, False, MUTED)
    textbox(slide, 11.6, 7.02, 1.1, 0.28, str(len(prs.slides.__iter__.__self__._sldIdLst)), 9.5, False, MUTED, PP_ALIGN.RIGHT)
    return slide


# ---------------- 1. 封面 ----------------
slide = prs.slides.add_slide(BLANK)
rect(slide, 0, 0, W, H, fill=INK, shape=MSO_SHAPE.RECTANGLE)
rect(slide, 0, 0, 7.4, H, fill=RGBColor(0x11, 0x1E, 0x38), shape=MSO_SHAPE.RECTANGLE)
rect(slide, 0.9, 1.5, 0.1, 1.5, fill=TEAL, shape=MSO_SHAPE.RECTANGLE)
textbox(slide, 1.2, 1.42, 8, 0.34, 'XH-202620 · 面向一流学科建设的学科垂类大模型与创新应用开发', 12, False, RGBColor(0x7D, 0xD3, 0xE6))
textbox(slide, 1.2, 1.82, 9, 0.9, '灵眸智教', 54, True, WHITE)
textbox(slide, 1.2, 2.82, 9.6, 0.5, '讲得对 · 查得到 · 能复核的学科助教', 22, False, RGBColor(0xCB, 0xE8, 0xF2))
textbox(slide, 1.2, 3.5, 9.2, 0.9,
        '计算机科学与技术学科 · 课程载体《数据结构与算法》\n'
        '12 个可编排智能体，覆盖 助学 / 助教 / 助研 全场景', 13.5, False, RGBColor(0x9F, 0xB4, 0xCC), spacing=1.5)
for index, (label, value) in enumerate([('12', '可编排智能体'), ('100%', '引用覆盖率'), ('34', '项自检全通过'), ('55', '学生规模化验证')]):
    x = 1.2 + index * 2.35
    rect(slide, x, 4.7, 2.05, 1.15, fill=RGBColor(0x1B, 0x2B, 0x47), radius=0.08)
    textbox(slide, x + 0.22, 4.86, 1.7, 0.44, value, 22, True, RGBColor(0x67, 0xE8, 0xF9))
    textbox(slide, x + 0.22, 5.34, 1.72, 0.3, label, 10, False, RGBColor(0x9F, 0xB4, 0xCC))
textbox(slide, 1.2, 6.5, 9, 0.3, '参赛团队：（团队名称）    指导教师：（待填）', 11, False, RGBColor(0x7D, 0x8F, 0xA8))

# ---------------- 2. 真问题 ----------------
slide = new_slide('一、作品阐述', '通用大模型在专业课上，错得很有说服力',
                  '我们先把「不可信」这件事量化：三类高频错误，学生几乎无法自查')
cards = [
    ('结论错而不自知', '问「快速排序最坏时间复杂度」，通用模型常答 O(n log n)。\n正确是 O(n²)——序列基本有序且固定取首元素为基准时退化。', '被问到的是「最坏」'),
    ('概念张冠李戴', '问「时间复杂度」，答的却是「空间复杂度」那一段。\n同章节、同术语，只差两个字，向量检索照样命中。', '时间 ↔ 空间 混淆'),
    ('给不出出处', '结论看似合理，但翻遍教材找不到对应原文。\n学生无法核验，教师不敢采用，最后退化成「随便问问」。', '引用是空的'),
]
for index, (head, body, tag) in enumerate(cards):
    x = 0.86 + index * 4.05
    rect(slide, x, 2.15, 3.85, 2.65, fill=WHITE, line=LINE)
    rect(slide, x, 2.15, 3.85, 0.06, fill=AMBER, shape=MSO_SHAPE.RECTANGLE)
    textbox(slide, x + 0.28, 2.38, 3.3, 0.34, head, 15, True, INK)
    textbox(slide, x + 0.28, 2.78, 3.3, 1.5, body, 11.5, False, SLATE, spacing=1.35)
    textbox(slide, x + 0.28, 4.32, 3.3, 0.3, '· ' + tag, 10.5, False, AMBER)
rect(slide, 0.86, 5.1, 11.6, 1.35, fill=AMBER_LIGHT, radius=0.05)
textbox(slide, 1.16, 5.26, 11.0, 0.34, '这三个问题，恰恰是教学场景里最不能容忍的三件事', 13.5, True, RGBColor(0x78, 0x35, 0x0F))
textbox(slide, 1.16, 5.66, 11.0, 0.6,
        '结论错 → 学生学错；概念错 → 越学越乱；无出处 → 教师无法背书。所以我们要做的不是「更会说话的模型」，'
        '而是「讲得对、查得到、能复核」的助教。', 11.5, False, RGBColor(0x92, 0x40, 0x0E), spacing=1.35)

# ---------------- 3. 解法三支柱 ----------------
slide = new_slide('一、作品阐述', '三根支柱，把「可信」做成工程约束', '不是文档里的承诺，而是每次回答都要过的三道关')
pillars = [
    ('01', '学科知识库', '11 篇 / 81 块结构化文档，教材·标准·大纲·案例·论文五类来源，\n每块带权威度(1–5)、作者、年份、页码。',
     '事实从哪来'),
    ('02', '可编排工作流', '12 个智能体 = 12 条带分支的工作流：检索 → 计算 → 生成 → 引用核验 → 轨迹留痕。',
     '答案怎么来'),
    ('03', '引用可追溯', '每个 [n] 都定位到「文档 · 章节 · 页码」，点开即见原文；\n未使用的引用会被自动剔除。',
     '凭什么信'),
]
for index, (number, head, body, tag) in enumerate(pillars):
    x = 0.86 + index * 4.05
    rect(slide, x, 2.1, 3.85, 3.0, fill=WHITE, line=LINE)
    textbox(slide, x + 0.28, 2.28, 0.8, 0.4, number, 22, True, RGBColor(0xB0, 0xD8, 0xE4))
    textbox(slide, x + 0.28, 2.72, 3.3, 0.36, head, 16, True, INK)
    textbox(slide, x + 0.28, 3.14, 3.3, 1.5, body, 11.5, False, SLATE, spacing=1.35)
    textbox(slide, x + 0.28, 4.68, 3.3, 0.3, '→ ' + tag, 10.5, True, TEAL)
rect(slide, 0.86, 5.42, 11.6, 1.05, fill=TEAL_LIGHT, radius=0.05)
textbox(slide, 1.16, 5.6, 11.0, 0.7,
        '关键取舍：宁可说「知识库未覆盖」，也不编造。没有 GPU、没有外网时自动切「离线检索模式」，'
        '并在界面顶部显式标注——不假装大模型在思考。', 12, True, RGBColor(0x0B, 0x5A, 0x70), spacing=1.35)

# ---------------- 4. 三场景 12 智能体 ----------------
slide = new_slide('二、功能思路', '三场景闭环：助学 · 助教 · 助研', '每个智能体都不是一句提示词，而是一条带步骤与分支的工作流')
scenarios = [
    ('助学', TEAL, ['智能答疑（多轮 + 指代消解）', '学习路径规划', '错题归因', '自测练习', '复杂问题求解']),
    ('助教', AMBER, ['智能备课', '作业与试题批改', '学情精准诊断', '课件素材生成']),
    ('助研', GREEN, ['前沿追踪', '学术写作辅助', '科研数据分析']),
]
for index, (name, color, items) in enumerate(scenarios):
    x = 0.86 + index * 4.05
    rect(slide, x, 2.05, 3.85, 4.35, fill=WHITE, line=LINE)
    rect(slide, x, 2.05, 3.85, 0.52, fill=color, shape=MSO_SHAPE.RECTANGLE)
    textbox(slide, x + 0.3, 2.15, 2.0, 0.34, name, 14, True, WHITE)
    textbox(slide, x + 2.4, 2.17, 1.2, 0.3, f'{len(items)} 个', 10.5, False, WHITE, PP_ALIGN.RIGHT)
    for row, item in enumerate(items):
        y = 2.78 + row * 0.66
        rect(slide, x + 0.28, y + 0.1, 0.1, 0.1, fill=color, shape=MSO_SHAPE.OVAL)
        textbox(slide, x + 0.5, y, 3.1, 0.5, item, 11.5, False, SLATE, spacing=1.2)
textbox(slide, 0.86, 6.52, 11.6, 0.3,
        '同一套引擎、同一套引用规则：换学科只需换知识库与提示词模板，工作流骨架不变。', 10.5, False, MUTED)

# ---------------- 4.5 课程空间：教学闭环 ----------------
slide = new_slide('二、功能思路', '课程空间：把 AI 接进真实教学流程（仿学习通）',
                  '只做聊天框，学生用完就走；真实课堂有组织形态——课程、任务点、测验、讨论、签到')
modules = [
    ('课程 / 任务点', '建课、邀请码加入、8 章 7 个任务点、学习时长入库', 'AI 课前导读 · 带 [n] 引用', TEAL),
    ('随堂测验', '按章节出 3–8 道单选，提交即判分、逐题解析', 'AI 命题 + 解析', TEAL),
    ('讨论区', '发帖后 AI 助教自动回帖，学生可继续追问老师', 'AI 助教 · ≤200 字', TEAL),
    ('签到', '教师发起 4 位码（10 分钟有效），签到率实时进学情', '规则校验，不用模型', MUTED),
    ('通知', '发布 / 置顶 / 未读红点 / 标记已读', '规则逻辑，不用模型', MUTED),
    ('学情看板', '个人与班级两套口径 + 预警名单（<60% / <60% / <80%）', 'AI 学情建议 · 只依据给定数字', TEAL),
]
for index, (name, body, ai, ai_color) in enumerate(modules):
    col, row = index % 3, index // 3
    x = 0.86 + col * 4.05
    y = 2.0 + row * 1.92
    rect(slide, x, y, 3.85, 1.72, fill=WHITE, line=LINE)
    textbox(slide, x + 0.26, y + 0.18, 3.2, 0.32, name, 13, True, INK)
    textbox(slide, x + 0.26, y + 0.56, 3.35, 0.72, body, 10, False, SLATE, spacing=1.3)
    rect(slide, x + 0.26, y + 1.3, 3.0, 0.3, fill=TEAL_LIGHT if ai_color == TEAL else BG, radius=0.3)
    textbox(slide, x + 0.38, y + 1.36, 2.9, 0.24, ai, 9.5, True,
            RGBColor(0x0B, 0x5A, 0x70) if ai_color == TEAL else MUTED)
textbox(slide, 0.86, 5.95, 11.6, 0.3,
        'AI 只用在该用的地方：4 处生成（导读 / 出题 / 助教回帖 / 学情建议）都挂「模型名 + 耗时」徽标，判分与签到不走模型。',
        10.5, False, MUTED)
textbox(slide, 0.86, 6.32, 11.6, 0.3,
        '边界说清楚：课程空间的 55 名学生与作答/签到记录是脚本生成的演示数据（结果确定、可复现），不代表真实教学行为。',
        10.5, False, AMBER)

# ---------------- 5. 一次答疑的链路 ----------------
slide = new_slide('二、功能思路', '一次答疑，六个可核查的环节', '评审可以在界面上逐段看到「它做了什么、依据是什么」')
steps = [
    ('① 多轮理解', '接上文、消歧\n「那它呢」→ 快速排序'),
    ('② 查询清洗', '剥离「什么/哪些/如何」\n只留学科术语'),
    ('③ 混合检索', 'BM25 + 向量 + 覆盖率\n+ 短语 + 权威度'),
    ('④ 学科消歧', '时间↔空间 / 实体一致性\n时间题不拿空间段'),
    ('⑤ 结构化生成', '结论 → 原文摘录\n→ 再想一想'),
    ('⑥ 引用核验', '未命中编号剔除\n命中 x/y 条写进轨迹'),
]
for index, (head, body) in enumerate(steps):
    x = 0.86 + index * 2.0
    rect(slide, x, 2.2, 1.86, 1.75, fill=WHITE, line=LINE)
    textbox(slide, x + 0.18, 2.32, 1.5, 0.3, head, 12, True, INK)
    textbox(slide, x + 0.18, 2.68, 1.55, 1.1, body, 9.5, False, SLATE, spacing=1.3)
    if index < 5:
        textbox(slide, x + 1.86, 2.9, 0.2, 0.3, '›', 16, True, MUTED)
rect(slide, 0.86, 4.25, 11.6, 1.15, fill=WHITE, line=LINE)
textbox(slide, 1.14, 4.38, 2.4, 0.3, '输出长什么样', 11, True, TEAL)
textbox(slide, 1.14, 4.7, 11.0, 0.6,
        '一句话结论（带 [n]） ┃ 知识原文摘录 + 「出处：图论基础与最短路径 · 最短路径」 ┃ 再想一想（启发式追问）',
        11, False, SLATE, spacing=1.3)
rect(slide, 0.86, 5.62, 5.72, 1.05, fill=TEAL_LIGHT, radius=0.05)
textbox(slide, 1.1, 5.78, 5.3, 0.34, '轨迹（trace）不是日志', 11.5, True, RGBColor(0x0B, 0x5A, 0x70))
textbox(slide, 1.1, 6.08, 5.3, 0.5, '每一步的输入、命中数、耗时都对评审可见', 10.5, False, RGBColor(0x0B, 0x5A, 0x70))
rect(slide, 6.74, 5.62, 5.72, 1.05, fill=AMBER_LIGHT, radius=0.05)
textbox(slide, 6.98, 5.78, 5.3, 0.34, '引用不是装饰', 11.5, True, RGBColor(0x78, 0x35, 0x0F))
textbox(slide, 6.98, 6.08, 5.3, 0.5, '生成时只用检索到的材料，用完还要回头核对编号', 10.5, False, RGBColor(0x92, 0x40, 0x0E))

# ---------------- 6. 检索消歧对比 ----------------
slide = new_slide('三、技术方案', '学科消歧：通用检索看不出的「差两个字」', '同一批问题，纯向量检索 vs 本平台检索的首条命中')
rows = [
    ('快速排序的「最坏时间复杂度」', '空间复杂度章节', '时间复杂度章节 + 排序总表'),
    ('「二叉排序树」退化成链表', '哈希表章节（同属查找结构）', ' BST 章节 + 平衡树'),
    ('建堆为什么是 O(n)', '堆排序流程章节', '树与二叉树 · 堆与优先队列'),
]
top = 2.15
col = [0.86, 4.9, 8.2, 12.46]
rect(slide, 0.86, top, 11.6, 0.46, fill=INK, shape=MSO_SHAPE.RECTANGLE)
for text, x0, x1 in [('提问', col[0], col[1]), ('纯向量检索首条', col[1], col[2]), ('本平台首条命中', col[2], col[3])]:
    textbox(slide, x0 + 0.2, top + 0.11, x1 - x0 - 0.3, 0.3, text, 10.5, True, WHITE)
for index, (question, wrong, right) in enumerate(rows):
    y = top + 0.46 + index * 0.72
    rect(slide, 0.86, y, 11.6, 0.72, fill=WHITE if index % 2 == 0 else RGBColor(0xFB, 0xFC, 0xFE), line=LINE)
    textbox(slide, col[0] + 0.2, y + 0.2, 3.7, 0.34, question, 10.5, True, INK)
    textbox(slide, col[1] + 0.2, y + 0.2, 3.0, 0.34, '×  ' + wrong, 10.5, False, RGBColor(0xB9, 0x1C, 0x1C))
    textbox(slide, col[2] + 0.2, y + 0.2, 4.0, 0.34, '✓  ' + right, 10.5, False, GREEN)
textbox(slide, 0.86, 4.62, 11.6, 0.3, '五层打分，逐层收紧', 12, True, INK)
layers = [
    ('BM25', '中文二元组，精确术语命中'),
    ('哈希向量', '512 维，近似语义'),
    ('覆盖率', 'idf 加权，防「碰一个词就上位」'),
    ('短语命中', '连续 n-gram 是强相关信号'),
    ('权威度', '教材 > 案例 > 网文'),
]
for index, (head, body) in enumerate(layers):
    x = 0.86 + index * 2.34
    rect(slide, x, 5.0, 2.2, 1.02, fill=WHITE, line=LINE)
    textbox(slide, x + 0.16, 5.1, 1.9, 0.3, head, 11, True, TEAL)
    textbox(slide, x + 0.16, 5.42, 1.9, 0.55, body, 9.5, False, SLATE, spacing=1.2)
rect(slide, 0.86, 6.18, 11.6, 0.5, fill=TEAL_LIGHT, radius=0.08)
textbox(slide, 1.1, 6.28, 11.2, 0.32,
        '在此之上再加两道学科规则：成对概念消歧（12 组）+ 实体一致性（30+ 算法与结构）', 11, True, RGBColor(0x0B, 0x5A, 0x70))

# ---------------- 7. 引用卡片 ----------------
slide = new_slide('三、技术方案', '「可追溯」的具体形态：一张引用卡片', '回答里的 [2] 点开后，看到的就是下面这些信息')
rect(slide, 0.86, 2.1, 7.35, 4.3, fill=WHITE, line=LINE)
textbox(slide, 1.12, 2.24, 3.0, 0.3, '回答正文（节选）', 10.5, True, MUTED)
textbox(slide, 1.12, 2.58, 6.85, 3.6,
        '一句话结论\n'
        '注意循环顺序必须是 k 在最外层，且 k 表示「允许经过的中间顶点集合逐步扩大」，'
        '顺序写错会得到错误结果。 [2]\n\n'
        '知识原文摘录\n'
        '……注意循环顺序必须是 k 在最外层，且 k 表示「允许经过的中间顶点集合逐步扩大」，'
        '顺序写错会得到错误结果。 [2]\n\n'
        '出处：图论基础与最短路径 · 最短路径',
        11.5, False, SLATE, spacing=1.4)
rect(slide, 8.45, 2.1, 4.01, 4.3, fill=WHITE, line=TEAL)
textbox(slide, 8.7, 2.24, 3.5, 0.3, '引用 [2] 详情', 10.5, True, TEAL)
fields = [
    ('定位', '图论基础与最短路径 · 最短路径'),
    ('原文片段', '注意循环顺序必须是 k 在最外层……'),
    ('来源类型', '教材'),
    ('权威度', '★★★★★ (5/5)'),
    ('相关度', '0.87'),
    ('作者/年份', '严蔚敏 等 · 2007'),
]
for index, (key, value) in enumerate(fields):
    y = 2.62 + index * 0.6
    textbox(slide, 8.7, y, 0.9, 0.26, key, 9.5, False, MUTED)
    textbox(slide, 8.7, y + 0.22, 3.5, 0.3, value, 10.5, True, INK)
textbox(slide, 0.86, 6.55, 11.6, 0.3,
        '同一套 locator 规则贯穿：答疑、批改、诊断、写作——凡是给结论，就给得出处。', 10.5, False, MUTED)

# ---------------- 8. 架构 ----------------
slide = new_slide('三、技术方案', '架构：一条请求经过的五层', '每层都可独立替换，也都能在没有 GPU / 没有外网的机器上跑通')
layers = [
    ('前端', '原生 JS + Tailwind，Flask 同源托管（无 CORS、无跨域 Cookie 问题）', TEAL_LIGHT, RGBColor(0x0B, 0x5A, 0x70)),
    ('应用层', 'Flask 蓝图：鉴权 / 资源 / 聊天（含 SSE 流式）/ 智能体 / 知识库 / 评测', WHITE, INK),
    ('工作流引擎', '声明式注册 → 检索 → 工具 → 条件分支 → 引用核验 → 轨迹', WHITE, INK),
    ('检索与生成', 'KB 混合检索（BM25 + 向量 + 消歧）｜ LLM 客户端（本地 / 星火 / OpenAI / 离线）', WHITE, INK),
    ('数据层', 'MySQL（首选）/ SQLite（自动降级）；会话·画像·反馈·埋点·评测', WHITE, INK),
]
for index, (name, body, fill, color) in enumerate(layers):
    y = 2.05 + index * 0.86
    rect(slide, 0.86, y, 2.1, 0.72, fill=INK, radius=0.06)
    textbox(slide, 1.02, y + 0.2, 1.8, 0.32, name, 11.5, True, WHITE)
    rect(slide, 3.1, y, 9.36, 0.72, fill=fill, line=LINE if fill == WHITE else None, radius=0.06)
    textbox(slide, 3.34, y + 0.2, 8.9, 0.36, body, 10.5, False, color)
rect(slide, 0.86, 6.42, 11.6, 0.44, fill=WHITE, line=LINE, radius=0.08)
textbox(slide, 1.1, 6.5, 11.2, 0.3,
        '零重型依赖启动：不装 torch 也能完整演示；装了 sentence-transformers 自动升级为语义向量后端', 10, False, MUTED)

# ---------------- 9. 模型与微调 ----------------
slide = new_slide('三、技术方案', '模型接入与学科微调', '四后端可切换，微调链路可复现')
rect(slide, 0.86, 2.05, 5.9, 3.05, fill=WHITE, line=LINE)
textbox(slide, 1.1, 2.16, 3.0, 0.3, '四种推理后端', 11, True, TEAL)
backends = [
    ('ollama（默认）', '本机 Qwen3-VL-8B，能识图 · 不出网'),
    ('local', '自备 HuggingFace 权重（纯文本，LLM_MODEL_PATH 指过去才启用）'),
    ('spark / openai', '星火或任意 OpenAI 兼容网关'),
    ('offline', '知识库原文结构化编排 + 显式标识'),
]
for index, (name, body) in enumerate(backends):
    y = 2.52 + index * 0.62
    textbox(slide, 1.1, y, 1.05, 0.28, name, 10.5, True, AMBER)
    textbox(slide, 2.2, y, 4.3, 0.28, body, 10.5, False, SLATE)
rect(slide, 6.96, 2.05, 5.5, 3.05, fill=WHITE, line=LINE)
textbox(slide, 7.2, 2.16, 4.5, 0.3, 'LoRA / SFT 链路', 11, True, TEAL)
train_steps = [
    ('build_dataset.py', '知识库章节 + 种子问答 → Alpaca 指令集'),
    ('train_lora.py', 'QLoRA r=16 / α=32 / 7 投影层'),
    ('只学回答', 'chat template + assistant 段 mask'),
    ('merge 导出', '合并权重，供 backend 直接加载'),
]
for index, (name, body) in enumerate(train_steps):
    y = 2.52 + index * 0.62
    textbox(slide, 7.2, y, 1.6, 0.28, name, 10, True, AMBER)
    textbox(slide, 8.85, y, 3.4, 0.28, body, 10, False, SLATE)
rect(slide, 0.86, 5.28, 11.6, 1.55, fill=WHITE, line=LINE)
textbox(slide, 1.1, 5.4, 5.0, 0.3, '为什么微调不是「先决条件」', 11.5, True, INK)
textbox(slide, 1.1, 5.74, 11.1, 1.0,
        '检索负责「事实」，微调负责「表达与规范」。没有微调，靠 RAG + 引用核验也能给出可核查的答案；'
        '有微调，则更贴合课程口径（术语、复杂度标注习惯、评分话术）。所以我们把两条路都做出来，且互不阻塞。',
        11, False, SLATE, spacing=1.4)

# ---------------- 10. 个性化 ----------------
slide = new_slide('三、技术方案', '个性化：画像驱动的四个环节', '同一问题，入门与提高两种画像，输出的深度与风格不同')
rect(slide, 0.86, 2.1, 3.5, 3.6, fill=WHITE, line=LINE)
textbox(slide, 1.1, 2.22, 3.0, 0.3, '学习画像', 11.5, True, TEAL)
profile_fields = ['知识水平：入门 / 进阶 / 提高', '学习目标：应试 / 项目 / 科研', '表达风格：均衡 / 简洁 / 启发 / 案例', '讲解深度：1–5 级', '薄弱点：自动累计']
for index, item in enumerate(profile_fields):
    y = 2.62 + index * 0.56
    rect(slide, 1.1, y + 0.09, 0.09, 0.09, fill=TEAL, shape=MSO_SHAPE.OVAL)
    textbox(slide, 1.32, y, 2.8, 0.4, item, 10.5, False, SLATE, spacing=1.2)
effects = [
    ('答疑深度', '入门先讲类比与直觉，提高直接上复杂度与变式'),
    ('路径规划', '按周拆解的学习阶段与资源清单'),
    ('练习推荐', '按掌握度选择基础题 / 迁移题'),
    ('错题归因', '按薄弱点选择追问角度'),
]
for index, (head, body) in enumerate(effects):
    x = 4.72
    y = 2.1 + index * 0.93
    rect(slide, x, y, 7.74, 0.8, fill=WHITE, line=LINE)
    textbox(slide, x + 0.24, y + 0.12, 1.5, 0.3, head, 11, True, INK)
    textbox(slide, x + 0.24, y + 0.44, 7.2, 0.3, body, 10.5, False, SLATE)
rect(slide, 0.86, 5.95, 11.6, 0.62, fill=AMBER_LIGHT, radius=0.08)
textbox(slide, 1.1, 6.06, 11.2, 0.42, '薄弱点由「作业得分 + 提问主题」共同聚合，可查看、可编辑，不做黑箱画像。', 11, True, RGBColor(0x78, 0x35, 0x0F))

# ---------------- 11. 运行效果 ----------------
slide = new_slide('四、运行效果', '实测：36 项自检全通过，12 题准确率 100%', '本机 Qwen3-VL-8B 生成与看图（RTX 4060 8GB，全本地不出网）；无模型时自动回落离线基线')
kpis = [('100%', '12 题准确率'), ('93.8%', '关键词平均召回'), ('≈38 s', '单题本机耗时'), ('5.8 GB', '本机模型权重'), ('36 / 36', '端到端自检')]
for index, (value, label) in enumerate(kpis):
    x = 0.86 + index * 2.36
    rect(slide, x, 2.1, 2.2, 1.05, fill=WHITE, line=LINE)
    textbox(slide, x + 0.2, 2.22, 1.85, 0.44, value, 20, True, TEAL)
    textbox(slide, x + 0.2, 2.72, 1.9, 0.3, label, 10, False, MUTED)
chart = CategoryChartData()
chart.categories = ['复杂度与\n排序稳定性', '图：最短路径', '二叉树：\n遍历与性质', '哈希与查找']
chart.add_series('平均分', (71.2, 74.0, 74.7, 77.1))
frame = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.86), Inches(3.35), Inches(6.5), Inches(3.2), chart)
plot = frame.chart
plot.has_legend = False
plot.has_title = True
plot.chart_title.text_frame.text = '各知识点平均分（真实作业成绩聚合）'
for paragraph in plot.chart_title.text_frame.paragraphs:
    for run in paragraph.runs:
        set_font(run, 11, True, SLATE)
plot.value_axis.has_major_gridlines = True
plot.value_axis.maximum_scale = 100
plot.value_axis.minimum_scale = 50
plot.value_axis.tick_labels.font.size = Pt(9)
plot.category_axis.tick_labels.font.size = Pt(9)
series = plot.plots[0].series[0]
series.format.fill.solid()
series.format.fill.fore_color.rgb = TEAL
series.has_data_labels = True
series.data_labels.font.size = Pt(9)
series.data_labels.number_format = '0.0'
series.data_labels.number_format_is_linked = False
rect(slide, 7.6, 3.35, 4.86, 3.2, fill=WHITE, line=LINE)
textbox(slide, 7.84, 3.47, 4.4, 0.3, '规模化使用（真实库统计）', 11, True, TEAL)
scale_rows = [('学生账号', '55 人'), ('作业 / 提交', '4 次 / 196 份'), ('已批改', '178 份'), ('平均分 / 提交率', '74.5 分 / 89.1%'), ('覆盖口径', '满足 ≥50 人')]
for index, (key, value) in enumerate(scale_rows):
    y = 3.82 + index * 0.48
    textbox(slide, 7.84, y, 2.4, 0.28, key, 10, False, MUTED)
    textbox(slide, 10.1, y, 2.2, 0.28, value, 10.5, True, INK, PP_ALIGN.RIGHT)
textbox(slide, 7.84, 6.14, 4.4, 0.3, '注：演示数据由脚本生成，非真实自然人', 9, False, AMBER, spacing=1.2)

# ---------------- 12. 典型案例 ----------------
slide = new_slide('四、运行效果', '三个典型案例，三种「可信」', '每个案例都能现场复现，输出与出处一并对得上')
cases = [
    ('案例一 · 概念辨析', 'Floyd 三重循环为何 k 在最外层',
     '结论：k 表示「允许经过的中间顶点集合逐步扩大」，顺序写错即错。\n'
     '核对：结论 / 原因 / 复杂度 / 适用条件 四项与教材一致，引用可点开原段。', '关键词召回 100%'),
    ('案例二 · 主观题批改', '快速排序排序 + 复杂度说明',
     '机器建议分 7.1 / 10，人工 7 分，误差 0.1。\n三条扣分依据（未分最好/最坏、空间漏算递归栈、基准选择）与人工判断一一对应。', '批改耗时 3 分钟 → 20 秒'),
    ('案例三 · 学情诊断', '全班 55 人、178 份已批改',
     '薄弱点由真实成绩聚合：复杂度与排序 71.2 分最低（不及格率 14.6%）。\n每个数字可回库核对，不是模型"猜"的。', '统计耗时 半天 → 3 秒'),
]
for index, (kicker, head, body, tag) in enumerate(cases):
    x = 0.86 + index * 4.05
    rect(slide, x, 2.1, 3.85, 3.85, fill=WHITE, line=LINE)
    textbox(slide, x + 0.26, 2.24, 3.3, 0.28, kicker, 9.5, True, TEAL)
    textbox(slide, x + 0.26, 2.56, 3.35, 0.66, head, 13, True, INK, spacing=1.25)
    textbox(slide, x + 0.26, 3.3, 3.35, 1.9, body, 10.5, False, SLATE, spacing=1.35)
    rect(slide, x + 0.26, 5.24, 3.3, 0.5, fill=TEAL_LIGHT, radius=0.1)
    textbox(slide, x + 0.42, 5.36, 3.0, 0.3, tag, 9.5, True, RGBColor(0x0B, 0x5A, 0x70))
textbox(slide, 0.86, 6.2, 11.6, 0.55,
        '三个案例的共同点：结论可对答案、过程可看轨迹、出处可点开——这也是评审现场最容易被验证的部分。',
        11, False, MUTED, spacing=1.35)

# ---------------- 13. 规模化与可复制 ----------------
slide = new_slide('五、可复制与可推广', '换一个学科，只改三个变量', '代码零改动；知识库与提示词是唯二的学科资产')
rect(slide, 0.86, 2.1, 5.6, 2.35, fill=INK, radius=0.06)
textbox(slide, 1.1, 2.24, 4.6, 0.3, '换学科', 11, True, RGBColor(0x7D, 0xD3, 0xE6))
code = ('DISCIPLINE_NAME = "临床医学"\n'
        'DISCIPLINE_COURSE = "内科学"\n'
        'KB_DOC_DIR = "data/kb_medicine"')
textbox(slide, 1.1, 2.62, 5.1, 1.3, code, 11.5, False, RGBColor(0xCB, 0xE8, 0xF2), spacing=1.5)
textbox(slide, 1.1, 3.98, 5.1, 0.3, '再点一次「重建索引」即完成迁移', 9.5, False, RGBColor(0x9F, 0xB4, 0xCC))
panels = [
    ('为什么能复制', '工作流骨架（检索→计算→生成→核验）与学科无关；\n学科差异只体现在知识库与提示词模板。'),
    ('为什么能推广', '零重型依赖：无 GPU / 无外网 / 无 MySQL 均可运行；\n一条命令启动，评审核验成本极低。'),
    ('规模化已验证', '55 名学生 / 4 次作业 / 196 份提交 / 240+ 使用事件，\n看板实时可查，满足 ≥50 人加分口径。'),
]
for index, (head, body) in enumerate(panels):
    y = 2.1 + index * 1.42
    rect(slide, 6.72, y, 5.74, 1.26, fill=WHITE, line=LINE)
    textbox(slide, 6.96, y + 0.14, 5.2, 0.3, head, 11.5, True, TEAL)
    textbox(slide, 6.96, y + 0.48, 5.25, 0.7, body, 10, False, SLATE, spacing=1.3)
rect(slide, 0.86, 4.68, 5.6, 1.75, fill=WHITE, line=LINE)
textbox(slide, 1.1, 4.8, 5.1, 0.3, '成本估算（单学科一年）', 11, True, INK)
cost_rows = [('知识库建设', '教师 20–40 小时'), ('部署与运维', '1 台 8C16G 服务器'), ('模型调用', '本地部署时零边际成本')]
for index, (key, value) in enumerate(cost_rows):
    y = 5.18 + index * 0.4
    textbox(slide, 1.1, y, 2.6, 0.28, key, 10, False, MUTED)
    textbox(slide, 3.6, y, 2.6, 0.28, value, 10, True, INK, PP_ALIGN.RIGHT)

# ---------------- 14. 迭代 + 结尾 ----------------
slide = new_slide('六、迭代计划', '下一步：从「课程级」走到「学科级」', '已经跑通的继续加固，尚未覆盖的明确排期')
plan = [
    ('已交付', '2026 Q3', '三场景 12 智能体 · 知识库与引用溯源 · 评测闭环 · 36 项自检'),
    ('已交付', '2026 Q4', '生成本机 Qwen3-VL-8B（识图 + 读文档）· 课程空间（任务点/测验/讨论/签到/学情）如何把 AI 接进课堂'),
    ('近期', '2027 Q1', '知识库扩到教材全文与论文库 · 同义词扩展提召回 · 课程空间接推理队列'),
    ('中期', '2027 H1', '多课程多学科租户化 · 批量批改与作业分发 · 学习行为驱动自适应推题'),
    ('远期', '2027 H2', '对接教务系统（课表/成绩）· 校级私有化部署 · 跨学科知识图谱'),
]
top, bottom = 2.08, 5.86            # 条目区上下边界，行数变化时自动压缩行距
gap = min(0.94, (bottom - top) / max(1, len(plan)))
row_h = gap - 0.16
for index, (stage, when, body) in enumerate(plan):
    y = top + index * gap
    rect(slide, 0.86, y, 1.5, row_h, fill=TEAL if index == 0 else WHITE, line=None if index == 0 else LINE, radius=0.08)
    textbox(slide, 0.98, y + row_h / 2 - 0.14, 1.3, 0.28, stage, 10.5, True, WHITE if index == 0 else INK)
    rect(slide, 2.5, y, 9.96, row_h, fill=WHITE, line=LINE, radius=0.06)
    textbox(slide, 2.74, y + 0.08, 1.2, 0.24, when, 9.5, True, AMBER)
    textbox(slide, 2.74, y + 0.34, 9.5, 0.28, body, 10, False, SLATE)
rect(slide, 0.86, 5.98, 11.6, 0.7, fill=INK, radius=0.08)
textbox(slide, 1.14, 6.12, 11.0, 0.44,
        '现场可验证：问一个易混问题看引用 · 提交一段"编造数据"看拦截 · 点开看板跑一次 12 题评测',
        11.5, True, RGBColor(0xCB, 0xE8, 0xF2))

prs.save(OUT)
print(f'已生成：{OUT}')
print(f'幻灯片数：{len(prs.slides)}')