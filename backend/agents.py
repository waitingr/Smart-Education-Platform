"""学科智能体与工作流编排引擎。

覆盖赛题要求的三类场景（助教 / 助学 / 助研），共 12 个智能体，每个智能体都是一条
「带检索、带工具、带条件分支」的工作流，执行过程完整记录到 trace，可在前端逐步骤展开。

设计要点：
- 所有智能体共用一套上下文（检索、生成、工具、留痕），新增场景只需注册一个函数；
- 关键结论强制引用 [n]，回答后做引用核验，未标注的自动补「参考来源」块；
- 内置学术诚信守卫，命中「编造文献 / 伪造数据」类请求时走拒绝分支并留痕；
- 未接入大模型时由离线编排器生成结构化内容，全链路依然可运行、可验收。
"""
import json
import os
import re
import time

from config import Config
from kb_service import get_kb
from llm_client import get_llm
from text_utils import clip, keywords, overlap_score, split_sentences

SCENARIOS = {
    'teach': {'name': '助教场景', 'icon': 'ph-chalkboard-teacher', 'desc': '面向教师备课、批改、学情诊断与资源生成'},
    'learn': {'name': '助学场景', 'icon': 'ph-student', 'desc': '面向学生答疑、路径规划、错题归因与自测'},
    'research': {'name': '助研场景', 'icon': 'ph-flask', 'desc': '面向前沿追踪、学术写作与科研数据分析'},
}

BASE_SYSTEM = f"""你是「灵眸智教」{Config.DISCIPLINE_NAME}学科垂类大模型助手，服务高校《{Config.DISCIPLINE_COURSE}》课程的师生。
学科范围：{Config.DISCIPLINE_ALIAS}。

硬性规则：
1. 只依据【检索资料】作答；资料不足时明确说明「知识库未覆盖」，并给出可执行的学习建议，严禁编造。
2. 每个关键结论后用方括号数字标注引用来源，例如 [1]、[2]；编号必须与【检索资料】中的编号一致。
   注意：直接写方括号数字本身，不要输出"编号"两个字，也不要写"[编号]"这样的占位符。
3. 使用 Markdown 输出，层级清晰；代码使用带语言标注的代码块。
4. 涉及算法必须给出时间复杂度与空间复杂度，并说明最好/平均/最坏情况。
5. 不生成违反学术诚信的内容：不编造文献、不伪造实验数据、不代写论文主体论证。
6. 使用简体中文，面向中国高校教学场景，语气专业、克制。"""

INTEGRITY_PATTERNS = [
    (re.compile(r'(编造|伪造|虚构|捏造|杜撰).{0,12}(文献|参考文献|引用|数据|实验结果|论文)'), '虚构文献或实验数据'),
    (re.compile(r'(生成|给我|帮我).{0,6}(假|伪).{0,4}(数据|实验结果)'), '伪造实验数据'),
    (re.compile(r'(不给|不要).{0,4}(真实|实际).{0,6}(数据|结果).{0,8}(写|生成)'), '在缺乏真实数据的情况下生成结论'),
    (re.compile(r'代写.{0,10}(论文|综述|开题报告)'), '代写学术作品'),
]


def check_academic_integrity(text):
    """学术诚信守卫。

    命中「编造/伪造/虚构 + 数据/文献」这类组合即拦截；但「如何避免编造数据」「禁止虚构文献」
    这类教学性表述属于正常提问，需要放行，否则会把合规讨论也挡在门外。
    """
    text = text or ''
    negations = ('避免', '不要', '不得', '禁止', '严禁', '不能', '不可', '勿', '防止', '杜绝')
    for pattern, reason in INTEGRITY_PATTERNS:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 10):match.start()]
            if any(word in prefix for word in negations):
                continue
            return {'blocked': True, 'reason': reason}
    return {'blocked': False, 'reason': ''}


# ==================== 执行上下文 ====================
class AgentContext:
    def __init__(self, agent_id, inputs, user=None, profile=None, history=None, session_id=None):
        self.agent_id = agent_id
        self.inputs = inputs or {}
        self.user = user
        self.profile = profile or {}
        self.history = history or []
        self.session_id = session_id
        self.trace = []
        self.facts = []
        self.citations = []
        self.artifacts = []
        self.degraded = True
        self.provider = 'offline'
        self.skipped = []
        self._step = 0

    # ---------- 输入 ----------
    def input(self, key, default=''):
        value = self.inputs.get(key)
        if value is None:
            return default
        if isinstance(value, str):
            value = value.strip()
            return value or default
        return value

    def all_text(self):
        """把所有文本型输入拼起来，供学术诚信守卫做全字段检测（避免换个输入框就绕过）。"""
        parts = []
        for value in self.inputs.values():
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return '\n'.join(parts)

    def flag(self, key, default=False):
        value = self.inputs.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on', '是')

    # ---------- 留痕 ----------
    def _entry(self, step_id, title, kind, detail='', status='ok', latency=0):
        self._step += 1
        entry = {
            'step': self._step,
            'id': step_id,
            'title': title,
            'kind': kind,
            'status': status,
            'detail': detail,
            'latency_ms': int(latency),
        }
        self.trace.append(entry)
        return entry

    def note(self, step_id, title, detail, kind='tool', status='ok', latency=0):
        return self._entry(step_id, title, kind, detail, status, latency)

    def skip(self, step_id, title, reason):
        self.skipped.append(step_id)
        return self._entry(step_id, title, 'branch', f'条件不满足，跳过：{reason}', status='skipped')

    def add_fact(self, text):
        if text and text not in self.facts:
            self.facts.append(text)

    # ---------- 检索 ----------
    def retrieve(self, step_id, title, query, top_k=None, prefer=None):
        started = time.time()
        try:
            results = get_kb().search(query, top_k=top_k, prefer_types=prefer)
        except Exception as error:
            self._entry(step_id, title, 'retrieval', f'检索失败：{error}', status='error',
                        latency=(time.time() - started) * 1000)
            return []
        self._merge_citations(results)
        if results:
            labels = {}
            for item in results:
                labels[item['source_type_label']] = labels.get(item['source_type_label'], 0) + 1
            summary = '、'.join(f'{key} {value} 条' for key, value in labels.items())
            self._entry(step_id, title, 'retrieval',
                        f"命中 {len(results)} 个知识块（{summary}），最高相关度 {results[0]['score']}",
                        latency=(time.time() - started) * 1000)
        else:
            self._entry(step_id, title, 'retrieval', '未命中知识库内容，将走降级分支', status='empty',
                        latency=(time.time() - started) * 1000)
        return results

    def _merge_citations(self, results):
        existing = {item['locator'] for item in self.citations}
        offset = len(self.citations)
        for item in results:
            if item['locator'] in existing:
                continue
            citation = dict(item)
            citation['index'] = len(self.citations) + 1
            self.citations.append(citation)
            existing.add(item['locator'])
        # 回填本轮检索结果的全局引用编号
        mapping = {item['locator']: item['index'] for item in self.citations}
        for item in results:
            item['index'] = mapping.get(item['locator'], item['index'] + offset)
        return results

    def material(self, text, name='附件', authority=4, source_label='附件'):
        """把附件/图片识别结果当作一个可引用的知识块并入本轮材料。

        附件不是知识库的一部分，但同样需要「有出处、可核对」：
        因此给它一个 _merge_citations 分配的正式引用编号，locator 记为「附件 · 文件名」。
        """
        text = (text or '').strip()
        if not text:
            return None
        name = (name or '附件').strip() or '附件'
        chunk = {
            'index': 0,
            'chunk_id': 'attachment',
            'doc_id': 'attachment',
            'doc_title': name,
            'section': '附件内容',
            'locator': f'附件 · {name}',
            'page': None,
            'text': text,
            'snippet': clip(text.replace('\n', ' '), 160),
            'score': 1.5,
            'coverage': 1.0,
            'source_type': 'attachment',
            'source_type_label': source_label,
            'authors': '',
            'year': '',
            'publisher': '',
            'authority': authority,
            'citation': f'附件 · {name}',
        }
        self._merge_citations([chunk])
        return chunk

    def context_block(self, results, max_chars=None):
        if max_chars is None:
            max_chars = 3600
            try:
                if get_llm().provider in ('ollama', 'local'):
                    max_chars = max(1200, Config.LLM_CONTEXT_CHARS // 2)
            except Exception:
                pass
        blocks, used = [], 0
        for item in results:
            text = item['text']
            if used + len(text) > max_chars:
                text = clip(text, max(0, max_chars - used))
            if not text:
                break
            blocks.append(f"[{item['index']}] 来源：{item['citation']}\n{text}")
            used += len(text)
            if used >= max_chars:
                break
        return '\n\n'.join(blocks)

    # ---------- 生成 ----------
    def ask(self, step_id, title, user_prompt, task='generic', chunks=None, facts=None,
            temperature=None, system_extra='', images=None):
        started = time.time()
        style = {
            'level': self.profile.get('level') or 'intermediate',
            'detail': self.profile.get('detail_level') or 'standard',
            'style': self.profile.get('learning_style') or 'balanced',
        }
        result = get_llm().complete(
            system=BASE_SYSTEM + ('\n\n' + system_extra if system_extra else ''),
            user=user_prompt,
            question=self.input('question') or self.input('topic') or user_prompt[:200],
            chunks=chunks or [],
            task=task,
            facts=facts if facts is not None else self.facts,
            style=style,
            temperature=temperature,
            images=images,
        )
        self.provider = result.provider
        self.degraded = result.degraded
        detail = f'{result.provider} / {result.model} · {result.latency_ms} ms'
        if result.degraded:
            detail += ' · 离线检索模式'
        self._entry(step_id, title, 'generation', detail, latency=(time.time() - started) * 1000)
        return result.text

    # ---------- 引用核验 ----------
    def verify_citations(self, answer):
        used = {int(match) for match in re.findall(r'\[(\d{1,2})\]', answer or '')}
        valid = {item['index'] for item in self.citations}
        hit = used & valid
        missing = sorted(valid - used)
        ratio = round(len(hit) / len(valid), 3) if valid else 0.0
        detail = f'引用命中 {len(hit)}/{len(valid)} 条' + (f'，未引用编号 {missing}' if missing else '')
        self._entry('verify', '引用与出处核验', 'guard', detail, status='ok' if (not valid or ratio >= 0.4) else 'warn')
        if self.citations and not hit:
            appendix = ['', '---', '**参考来源（自动补全，便于核查）**']
            for item in self.citations[:5]:
                appendix.append(f"- [{item['index']}] {item['citation']}：{clip(item['snippet'], 80)}")
            answer = (answer or '') + '\n'.join(appendix)
        return answer, ratio

    # ---------- 结果 ----------
    def result(self, answer, extra=None):
        answer, citation_ratio = self.verify_citations(answer)
        payload = {
            'agent_id': self.agent_id,
            'answer': answer,
            'citations': self.citations,
            'trace': self.trace,
            'facts': self.facts,
            'artifacts': self.artifacts,
            'degraded': self.degraded,
            'provider': self.provider,
            'citation_ratio': citation_ratio,
            'ai_generated': True,
            'ai_label': Config.AI_CONTENT_LABEL,
            'scenario': AGENTS[self.agent_id]['scenario'] if self.agent_id in AGENTS else None,
            'agent_name': AGENTS[self.agent_id]['name'] if self.agent_id in AGENTS else self.agent_id,
            'discipline': Config.DISCIPLINE_NAME,
            'course': Config.DISCIPLINE_COURSE,
        }
        if extra:
            payload.update(extra)
        return payload


# ==================== 智能体注册 ====================
AGENTS = {}


def register(agent_id, name, scenario, icon, summary, task, inputs, steps, tags=None):
    def decorator(function):
        AGENTS[agent_id] = {
            'id': agent_id,
            'name': name,
            'scenario': scenario,
            'scenario_label': SCENARIOS.get(scenario, {}).get('name', scenario),
            'icon': icon,
            'summary': summary,
            'task': task,
            'inputs': inputs,
            'steps': steps,
            'tags': tags or [],
            'run': function,
        }
        return function
    return decorator


def list_agents():
    grouped = {}
    for agent in AGENTS.values():
        grouped.setdefault(agent['scenario'], []).append({
            'id': agent['id'],
            'name': agent['name'],
            'icon': agent['icon'],
            'summary': agent['summary'],
            'inputs': agent['inputs'],
            'steps': agent['steps'],
            'tags': agent['tags'],
            'scenario': agent['scenario'],
            'scenario_label': agent['scenario_label'],
        })
    return [
        {
            'id': key,
            'name': SCENARIOS.get(key, {}).get('name', key),
            'icon': SCENARIOS.get(key, {}).get('icon', 'ph-sparkle'),
            'desc': SCENARIOS.get(key, {}).get('desc', ''),
            'agents': value,
        }
        for key, value in grouped.items()
    ]


# ==================== 辅助工具 ====================
def _profile_line(profile):
    if not profile:
        return '未设置画像（默认按本科二年级中等水平作答）'
    level = {'beginner': '入门', 'intermediate': '中等', 'advanced': '进阶'}.get(profile.get('level'), '中等')
    style = {'socratic': '启发式追问', 'direct': '直接给结论', 'balanced': '先结论后展开'}.get(profile.get('learning_style'), '平衡')
    detail = {'brief': '精简', 'standard': '标准', 'deep': '深入'}.get(profile.get('detail_level'), '标准')
    weak = '、'.join(profile.get('weak_points') or []) or '暂无记录'
    goal = profile.get('goal') or '未填写'
    return f'水平：{level}；讲解风格：{style}；详细程度：{detail}；学习目标：{goal}；薄弱点：{weak}'


def _resolve_query(question, history):
    """多轮指代消解：短问题且含指代词时，用上一轮用户问题补全检索语义。"""
    question = (question or '').strip()
    if not history:
        return question
    pronouns = ('这个', '它', '上面', '刚才', '那个', '这道', '继续', '还有', '再说', '为什么')
    if len(question) <= 16 and any(token in question for token in pronouns):
        last_user = next((item for item in reversed(history) if item.get('role') == 'user'), None)
        if last_user and last_user.get('content'):
            return f"{clip(last_user['content'], 80)} {question}"
    return question


def _knowledge_numbers(chunks):
    facts = []
    for chunk in chunks[:3]:
        for sentence in split_sentences(chunk['text']):
            if '复杂度' in sentence and ('O(' in sentence or 'O（' in sentence):
                facts.append(f"复杂度依据（{chunk['locator']}）：{clip(sentence, 90)}")
                break
    return facts[:2]


def _heuristic_score(question, answer, reference, full_score):
    """无大模型时的可解释评分：关键点覆盖 + 篇幅充分度 + 结构规范。"""
    key_source = (reference or '') + ' ' + (question or '')
    coverage = overlap_score(key_source, answer)
    length_factor = min(1.0, len(answer) / max(60, len(key_source) * 0.35))
    structure = 0.08 if re.search(r'(\n\s*[-*]\s|\n\s*\d+[.、]|首先|其次|最后)', answer or '') else 0.0
    ratio = min(1.0, 0.5 + 0.32 * coverage + 0.18 * length_factor + structure)
    score = round(full_score * max(0.35, ratio), 1)
    breakdown = [
        f'关键点覆盖度 {round(coverage * 100, 1)}%（与题目/参考答案的关键术语重合比例）',
        f'篇幅充分度 {round(length_factor * 100, 1)}%',
        f'结构规范性 {"具备分点论述" if structure else "缺少分点，建议按要点组织"}',
    ]
    return score, breakdown


def _table_stats(path):
    from doc_parser import numeric_summary, parse_tabular
    table = parse_tabular(path)
    header, rows = table['header'], table['rows']
    stats = []
    charts = []
    for column_index, column_name in enumerate(header[:8]):
        values = [row[column_index] for row in rows if column_index < len(row)]
        summary = numeric_summary(values)
        if summary.get('count', 0) >= 3:
            stats.append({
                'column': str(column_name) or f'第{column_index + 1}列',
                'summary': summary,
            })
    if stats:
        charts.append({
            'type': 'bar',
            'title': f"{stats[0]['column']} 分布概览",
            'labels': ['最小值', 'P25', '中位数', 'P75', '最大值'],
            'values': [stats[0]['summary'].get(key, 0) for key in ('min', 'p25', 'median', 'p75', 'max')],
        })
    return {'header': header, 'rows': rows, 'row_count': len(rows), 'stats': stats, 'charts': charts, 'sheet': table.get('sheet')}


# ==================== 助教场景 ====================
@register(
    'lesson_plan', '智能备课', 'teach', 'ph-chalkboard-teacher',
    '基于课程标准与教材自动生成教学设计、时间分配、课堂活动与作业',
    'lesson_plan',
    [
        {'key': 'topic', 'label': '授课主题', 'type': 'text', 'required': True, 'placeholder': '例如：二叉排序树'},
        {'key': 'duration', 'label': '课时长度', 'type': 'select', 'options': ['45分钟', '2学时', '3学时'], 'default': '2学时'},
        {'key': 'level', 'label': '授课对象', 'type': 'select', 'options': ['本科一年级', '本科二年级', '本科三年级', '研究生'], 'default': '本科二年级'},
        {'key': 'requirements', 'label': '补充要求', 'type': 'textarea', 'placeholder': '例如：需要有课堂互动环节、结合考研真题'},
    ],
    ['检索课标与教材', '生成教学目标与重难点', '设计教学环节与时间分配', '生成课堂活动与提问', '学术规范校验'],
    tags=['课程标准', '教学设计', '课堂活动'],
)
def run_lesson_plan(ctx):
    topic = ctx.input('topic', '二叉排序树')
    duration = ctx.input('duration', '2学时')
    level = ctx.input('level', '本科二年级')
    requirements = ctx.input('requirements', '无')
    chunks = ctx.retrieve('retrieve', '检索课程标准与教材章节', f'{topic} 教学目标 重点 难点 教材章节', top_k=5)
    if not chunks:
        ctx.skip('outline', '生成时间分配', '未检索到教材内容')

    minutes = {'45分钟': 45, '2学时': 90, '3学时': 135}.get(duration, 90)
    plan = [
        f'导入与动机（{round(minutes * 0.1)} 分钟）',
        f'概念讲解（{round(minutes * 0.35)} 分钟）',
        f'示例演示与手工追踪（{round(minutes * 0.25)} 分钟）',
        f'课堂练习与互评（{round(minutes * 0.2)} 分钟）',
        f'总结与作业布置（{round(minutes * 0.1)} 分钟）',
    ]
    ctx.add_fact('课时总长：' + duration + f'（约 {minutes} 分钟）')
    for item in plan:
        ctx.add_fact('时间分配：' + item)
    for fact in _knowledge_numbers(chunks):
        ctx.add_fact(fact)

    context = ctx.context_block(chunks)
    prompt = f"""【任务】为《{Config.DISCIPLINE_COURSE}》设计一次 {duration} 的教学方案。
【授课主题】{topic}
【授课对象】{level}
【教师补充要求】{requirements}
【检索资料】
{context}

【输出要求】严格按以下结构输出 Markdown：
## 一、教学目标（知识/能力/素养三条，可测量）
## 二、重点与难点（各 2 条，说明难点成因）
## 三、教学环节与时间分配（表格：环节 | 时长 | 教师活动 | 学生活动）
## 四、核心内容讲解要点（3~5 条，含代码或图示说明，必须标注引用编号）
## 五、课堂活动与提问设计（含 1 个易错点辨析）
## 六、作业与评价（分层：基础/提高/拓展）
## 七、板书设计（ASCII 或列表）"""
    answer = ctx.ask('generate', '生成教学设计', prompt, task='lesson_plan', chunks=chunks)
    return ctx.result(answer)


@register(
    'grading', '作业与试题批改', 'teach', 'ph-check-square-offset',
    '客观题自动判分、主观题按评分点给出依据与改进建议',
    'grading',
    [
        {'key': 'question', 'label': '题目/题干', 'type': 'textarea', 'required': True, 'placeholder': '粘贴题目或作业要求'},
        {'key': 'student_answer', 'label': '学生作答', 'type': 'textarea', 'required': True, 'placeholder': '粘贴学生提交的答案或代码'},
        {'key': 'reference', 'label': '参考答案（可选）', 'type': 'textarea', 'placeholder': '不填则由系统依据知识库生成评分点'},
        {'key': 'full_score', 'label': '满分', 'type': 'number', 'default': '10'},
    ],
    ['学术诚信检查', '检索评分依据', '拆解评分点', '逐点评分与反馈', '生成改进建议'],
    tags=['自动批改', '评分依据', '学术诚信'],
)
def run_grading(ctx):
    question = ctx.input('question')
    student_answer = ctx.input('student_answer')
    reference = ctx.input('reference')
    try:
        full_score = float(ctx.input('full_score', '10'))
    except (TypeError, ValueError):
        full_score = 10.0

    guard = check_academic_integrity(student_answer or ctx.all_text())
    if guard['blocked']:
        ctx.note('guard', '学术诚信检查', f"命中风险：{guard['reason']}", kind='guard', status='blocked')
        answer = (
            f"### 批改结果：暂不受理\n\n提交内容被学术诚信守卫拦截，命中规则：**{guard['reason']}**。\n\n"
            "按课程规范，作业必须由本人独立完成，不得编造实验数据或虚构参考文献。"
            "请修改后重新提交；如为误判，请在反馈中说明具体上下文，教师可人工复核。"
        )
        return ctx.result(answer, {'score': None, 'blocked': True})
    ctx.note('guard', '学术诚信检查', '未命中风险规则，进入评分流程', kind='guard')

    chunks = ctx.retrieve('retrieve', '检索评分依据', question, top_k=4)
    score, breakdown = _heuristic_score(question, student_answer, reference, full_score)
    ctx.add_fact(f'机器建议分：{score} / {full_score}（需教师确认后生效）')
    for item in breakdown:
        ctx.add_fact('评分依据：' + item)

    context = ctx.context_block(chunks)
    prompt = f"""【任务】批改一道《{Config.DISCIPLINE_COURSE}》作业，满分 {full_score} 分。
【题目】{question}
【参考答案】{reference or '（未提供，请依据检索资料与学科规范推导评分点）'}
【学生作答】
{student_answer}
【检索资料】
{context}

【输出要求】
## 一、评分点拆解（表格：评分点 | 分值 | 判定标准）
## 二、逐项判定（对照学生作答，指出得分/失分及依据，标注引用编号）
## 三、总分与等级（给出建议分数与依据，说明扣分位置）
## 四、改进建议（3 条，具体到可执行的修改动作）
## 五、规范性问题（复杂度标注、命名、边界处理等）"""
    answer = ctx.ask('generate', '生成评分与反馈', prompt, task='grading', chunks=chunks)
    answer = f"**机器建议分：{score} / {full_score:g}**（教师可在右侧直接修正）\n\n" + answer
    return ctx.result(answer, {'score': score, 'full_score': full_score, 'blocked': False})


@register(
    'class_diagnosis', '学情精准诊断', 'teach', 'ph-chart-line-up',
    '汇聚作业与测验数据，识别知识薄弱点并生成预警报告',
    'diagnosis',
    [
        {'key': 'scope', 'label': '诊断范围', 'type': 'select', 'options': ['全班', '单个学生'], 'default': '全班'},
        {'key': 'student_number', 'label': '学号（单学生时填写）', 'type': 'text', 'placeholder': '例如：2023010101'},
        {'key': 'focus', 'label': '关注知识点（可选）', 'type': 'text', 'placeholder': '例如：图的最短路径'},
    ],
    ['汇聚作业与测验数据', '计算班级指标', '识别薄弱知识点', '生成诊断与预警', '教学干预建议'],
    tags=['学情分析', '预警', '数据驱动'],
)
def run_class_diagnosis(ctx):
    from models import Homework, HomeworkSubmission, User

    scope = ctx.input('scope', '全班')
    student_number = ctx.input('student_number')
    focus = ctx.input('focus', '')
    started = time.time()
    try:
        homeworks = Homework.query.all()
        submissions = HomeworkSubmission.query.all()
        student_count = User.query.filter_by(role='student').count()
    except Exception as error:
        ctx.note('stats', '汇聚学习数据', f'数据读取失败：{error}', status='error')
        homeworks, submissions, student_count = [], [], 0
        scope = '全班'

    scores = [item.score for item in submissions if item.score is not None]
    avg = round(sum(scores) / len(scores), 1) if scores else 0
    completion = round(len(submissions) / max(1, len(homeworks) * student_count) * 100, 1) if homeworks else 0
    distribution = {'优秀(≥90)': 0, '良好(80-89)': 0, '中等(70-79)': 0, '及格(60-69)': 0, '不及格(<60)': 0}
    for value in scores:
        if value >= 90:
            distribution['优秀(≥90)'] += 1
        elif value >= 80:
            distribution['良好(80-89)'] += 1
        elif value >= 70:
            distribution['中等(70-79)'] += 1
        elif value >= 60:
            distribution['及格(60-69)'] += 1
        else:
            distribution['不及格(<60)'] += 1

    ctx.note('stats', '汇聚学习数据',
             f'作业 {len(homeworks)} 次 / 提交 {len(submissions)} 份 / 学生 {student_count} 人 / 平均分 {avg}',
             latency=(time.time() - started) * 1000)
    ctx.add_fact(f'作业总次数：{len(homeworks)}；已批改份数：{len(scores)}；学生人数：{student_count}')
    ctx.add_fact(f'平均分：{avg} 分；整体提交率：{completion}%')
    if student_count >= 50:
        ctx.add_fact(f'已满足规模化使用口径：覆盖 {student_count} 名学生（≥50 人）')

    # 按作业（知识点）聚合真实得分，定位薄弱点，而不是凭检索猜一个易错点
    by_homework = {}
    for item in submissions:
        if item.score is None:
            continue
        by_homework.setdefault(item.homework_id, []).append(item.score)
    per_point = []
    for homework in homeworks:
        values = by_homework.get(homework.id) or []
        if not values:
            continue
        fail_rate = sum(1 for value in values if value < 60) / len(values) * 100
        per_point.append({
            'title': homework.title,
            'mean': round(sum(values) / len(values), 1),
            'fail': round(fail_rate, 1),
            'count': len(values),
        })
    per_point.sort(key=lambda item: item['mean'])
    weak_points = [item for item in per_point if item['mean'] < 75 or item['fail'] >= 20][:3]
    for item in weak_points:
        ctx.add_fact(f'薄弱知识点：{item["title"]}｜平均 {item["mean"]} 分｜不及格率 {item["fail"]}%（{item["count"]} 份）')
    if per_point and (not weak_points or per_point[-1]['mean'] >= 80):
        strongest = per_point[-1]
        ctx.add_fact(f'掌握较好：{strongest["title"]}｜平均 {strongest["mean"]} 分｜不及格率 {strongest["fail"]}%')
    if per_point:
        ctx.artifacts.append({
            'type': 'bar',
            'title': '各知识点平均分',
            'labels': [item['title'][:10] for item in per_point],
            'values': [item['mean'] for item in per_point],
        })
    ctx.artifacts.append({
        'type': 'bar',
        'title': '成绩分布',
        'labels': list(distribution.keys()),
        'values': list(distribution.values()),
    })
    ctx.artifacts.append({
        'type': 'kpi',
        'items': [
            {'label': '平均分', 'value': avg},
            {'label': '提交率', 'value': f'{completion}%'},
            {'label': '学生数', 'value': student_count},
            {'label': '作业次数', 'value': len(homeworks)},
        ],
    })

    uncertain = []
    if student_number:
        student = User.query.filter_by(student_number=student_number).first()
        if student:
            own = [item for item in submissions if item.student_id == student.id]
            own_scores = [item.score for item in own if item.score is not None]
            ctx.add_fact(f'学生 {student.username}（{student_number}）提交 {len(own)} 次，'
                         f'平均 {round(sum(own_scores) / len(own_scores), 1) if own_scores else 0} 分')
            uncertain = [item for item in own if item.score is None]
        else:
            ctx.add_fact(f'未找到学号 {student_number} 的学生记录')

    weak_topics = ' '.join(item['title'] for item in weak_points)
    query = f'{focus} 易错点' if focus else (f'{weak_topics} 易错点 难点' if weak_topics else '常见错误 易错点 难点')
    chunks = ctx.retrieve('retrieve', '检索知识点易错分析', query, top_k=4)
    if not chunks:
        ctx.skip('generate_weak', '生成干预建议', '无知识库线索')

    context = ctx.context_block(chunks)
    prompt = f"""【任务】基于真实学习数据生成《{Config.DISCIPLINE_COURSE}》学情诊断报告。
【诊断范围】{scope}{('（学号 ' + student_number + '）') if student_number else ''}
【关注知识点】{focus or '全课程'}
【数据事实】
{chr(10).join('- ' + fact for fact in ctx.facts)}
【检索资料】
{context}

【输出要求】
## 一、总体学情（结合数据给出判断，不夸大）
## 二、薄弱知识点识别（表格：知识点 | 证据 | 严重程度）
## 三、分层学生画像（优秀/中等/需关注三类特征）
## 四、预警提示（需关注的学生或风险点，说明触发条件）
## 五、教学干预建议（按优先级排序，可执行）
## 六、下一次课的重点复习清单"""
    answer = ctx.ask('generate', '生成诊断报告', prompt, task='diagnosis', chunks=chunks)
    return ctx.result(answer, {'metrics': {'avg_score': avg, 'completion_rate': completion, 'student_count': student_count, 'homework_count': len(homeworks)}})


@register(
    'material_gen', '课件与试题生成', 'teach', 'ph-presentation-chart',
    '生成课件大纲、课堂练习与配套代码示例，自动标注难度与答案',
    'material',
    [
        {'key': 'topic', 'label': '主题', 'type': 'text', 'required': True, 'placeholder': '例如：快速排序'},
        {'key': 'kind', 'label': '生成类型', 'type': 'select', 'options': ['课件大纲', '课堂练习', '代码示例', '随堂测验'], 'default': '课件大纲'},
        {'key': 'count', 'label': '数量', 'type': 'number', 'default': '5'},
        {'key': 'difficulty', 'label': '难度', 'type': 'select', 'options': ['基础', '中等', '综合'], 'default': '中等'},
    ],
    ['检索教材内容', '生成大纲/题目', '标注难度与答案', '规范性与学术校验'],
    tags=['课件', '试题', '代码示例'],
)
def run_material_gen(ctx):
    topic = ctx.input('topic', '快速排序')
    kind = ctx.input('kind', '课件大纲')
    count = ctx.input('count', '5')
    difficulty = ctx.input('difficulty', '中等')
    chunks = ctx.retrieve('retrieve', '检索教材与案例', f'{topic} 核心要点 例题', top_k=5)
    for fact in _knowledge_numbers(chunks):
        ctx.add_fact(fact)
    context = ctx.context_block(chunks)
    prompt = f"""【任务】为《{Config.DISCIPLINE_COURSE}》的「{topic}」生成{kind}，难度为{difficulty}，数量 {count}。
【检索资料】
{context}

【输出要求】
- 若为「课件大纲」：给出 8~12 页的幻灯片大纲（每页：标题 + 3 条要点 + 配图建议）。
- 若为「课堂练习」或「随堂测验」：每题包含【题目】【难度】【参考答案】【解析】【对应知识点】，并标注引用编号。
- 若为「代码示例」：给出可运行代码 + 逐行注释 + 复杂度分析。
- 结尾给出「常见错误提醒」与「评分要点」。"""
    answer = ctx.ask('generate', f'生成{kind}', prompt, task='material', chunks=chunks)
    return ctx.result(answer)


# ==================== 助学场景 ====================
@register(
    'tutor', '知识问答与分步讲解', 'learn', 'ph-chat-circle-dots',
    '多轮对话答疑，支持概念辨析、公式推导与代码调试的分步引导',
    'qa',
    [
        {'key': 'question', 'label': '你的问题', 'type': 'textarea', 'required': True, 'placeholder': '例如：为什么快速排序最坏是 O(n²)？'},
        {'key': 'socratic', 'label': '苏格拉底式引导', 'type': 'switch', 'default': True},
    ],
    ['多轮指代消解', '检索学科知识库', '分步讲解生成', '引用核验', '追问建议'],
    tags=['多轮对话', '分步讲解', '可追溯'],
)
def run_tutor(ctx):
    question = ctx.input('question')
    socratic = ctx.flag('socratic', True)
    resolved = _resolve_query(question, ctx.history)
    if resolved != question:
        ctx.note('resolve', '多轮指代消解', f'「{clip(question, 30)}」→「{clip(resolved, 60)}」', kind='branch')
    else:
        ctx.note('resolve', '多轮指代消解', '当前问题独立完整，无需补全上下文', kind='branch')

    chunks = ctx.retrieve('retrieve', '检索学科知识库', resolved, top_k=5)
    if not chunks:
        ctx.skip('explain', '分步讲解', '知识库未命中')

    # 附件（含图片识别结果）作为最高优先级的材料并入本轮上下文
    material = None
    images = []
    image_citation = None
    attachment_path = ctx.input('attachment_path')
    attachment_name = ctx.input('attachment_name', '附件')
    if attachment_path and os.path.isfile(attachment_path):
        from llm_client import get_llm
        vision_model = get_llm().model_name
        if get_llm().supports_images():
            from vision_service import image_data_url
            data_url = image_data_url(attachment_path)
            if data_url:
                images = [data_url]
                # 直达看图也必须留出处：登记一条「附件 · 文件名」引用（不进上下文，避免干扰作答）
                image_citation = ctx.material(f'【图片附件】{attachment_name}', attachment_name)
                ctx.note('attach', '图片直读',
                         f"图片「{attachment_name}」已直接送入模型（{len(data_url) // 1024} KB），无需先转文字",
                         kind='tool')
        if not images:
            from vision_service import get_vision
            description, info = get_vision().describe(attachment_path)
            if description:
                material = ctx.material(
                    f'【图片内容识别 · {info.get("model", "VLM")}】\n{description}',
                    ctx.input('attachment_name', '附件'))
                ctx.note('attach', '图片转写为文字', '当前模型不支持直接看图，已先用视觉模型转写后参与作答',
                         kind='tool', status='warn')
    if material is None:
        material = ctx.material(ctx.input('attachment_text'), ctx.input('attachment_name', '附件'))
    if material is not None:
        ctx.note('attach', '附件内容并入材料',
                 f"附件「{ctx.input('attachment_name', '附件')}」已识别 {len(material['text'])} 字，参与本轮作答",
                 kind='tool')
        # 再按附件内容反查知识库：把「图里讲了什么」和「教材里对应哪一节」连起来
        related = ctx.retrieve('relate', '按附件内容检索相关知识点', material['text'][:300], top_k=3)
        chunks = [material] + (related or chunks)

    # 条件分支：命中较弱时走降级讲解 + 换问法建议
    low_confidence = (not chunks) or (chunks[0]['coverage'] < 0.12 and chunks[0]['score'] < 0.45)
    if low_confidence:
        ctx.note('branch', '置信度分支', '相关度偏低，启用「边界说明 + 换问法建议」分支', kind='branch', status='warn')

    context = ctx.context_block(chunks)
    history_text = '\n'.join(
        f"{'学生' if item.get('role') == 'user' else '助教'}：{clip(item.get('content', ''), 200)}"
        for item in ctx.history[-6:]
    ) or '（本轮为会话首轮）'
    style_hint = (
        '采用苏格拉底式引导：先提问启发，再给出结论，最后总结方法。'
        if socratic else
        '采用直接讲授式：先给结论，再给分步推导。'
    )
    weak = '、'.join(ctx.profile.get('weak_points') or [])
    prompt = f"""【任务】回答学生的问题，做到准确、可追溯、分步引导。
【学生画像】{_profile_line(ctx.profile)}
【历史对话】
{history_text}
【本轮问题】{question}
【检索资料】
{context}

【讲解风格】{style_hint}
【输出要求】
## 一句话结论
## 分步讲解（3~6 步，每步一句结论 + 依据，关键结论用 [1] [2] 这种方括号数字标注）
## 代码或公式示例（如适用，附复杂度分析）
## 易错点与边界情况
## 再想一想（2 个递进问题）
{f'【提示】该学生历史薄弱点为：{weak}，请在讲解中针对性补强。' if weak else ''}
{f'【注意】检索相关度偏低，若资料不足请明确说明「知识库未覆盖」，不要编造。' if low_confidence else ''}"""
    answer = ctx.ask('explain', '生成分步讲解', prompt, task='tutor', chunks=chunks, images=images)
    if image_citation is not None:
        # 图片来自用户上传，不是知识库；但同样要能点回原文件核对
        answer = ((answer or '').rstrip()
                  + f"\n\n> 图片依据：[{image_citation['index']}] 附件 · {attachment_name}"
                  + f"（本机视觉模型 {vision_model} 直接读取原图）")
    suggestions = []
    if chunks:
        suggestions = [clip(item['sentence'], 40) for item in _top_sentences(resolved, chunks, 3)]
    return ctx.result(answer, {'suggestions': suggestions, 'resolved_query': resolved, 'low_confidence': low_confidence})


def _top_sentences(question, chunks, limit=3):
    scored = []
    for chunk in chunks:
        for sentence in split_sentences(chunk['text']):
            if len(sentence) < 10:
                continue
            scored.append({'sentence': sentence, 'score': overlap_score(question, sentence)})
    scored.sort(key=lambda item: item['score'], reverse=True)
    return scored[:limit]


@register(
    'learning_path', '个性化学习路径', 'learn', 'ph-path',
    '依据知识水平与目标动态规划阶段性学习路径与检验方式',
    'path',
    [
        {'key': 'goal', 'label': '学习目标', 'type': 'text', 'required': True, 'placeholder': '例如：三周内掌握图算法并完成课程项目'},
        {'key': 'weeks', 'label': '周期（周）', 'type': 'number', 'default': '4'},
        {'key': 'hours', 'label': '每周可投入（小时）', 'type': 'number', 'default': '6'},
    ],
    ['读取学习者画像', '目标拆解', '检索配套资源', '生成周计划', '设置检验点'],
    tags=['个性化', '自适应', '学习路径'],
)
def run_learning_path(ctx):
    goal = ctx.input('goal', '系统掌握数据结构与算法')
    weeks = max(1, min(int(ctx.input('weeks', '4') or 4), 24))
    hours = ctx.input('hours', '6')
    level = ctx.profile.get('level') or 'intermediate'
    weak = ctx.profile.get('weak_points') or []
    ctx.note('profile', '读取学习者画像',
             f"水平 {level}；薄弱点 {'、'.join(weak) if weak else '暂无记录'}；目标：{clip(goal, 40)}", kind='tool')

    chunks = ctx.retrieve('retrieve', '检索配套学习资源', f'{goal} 核心知识点 学习顺序', top_k=6)
    stage_names = ['基础补齐', '核心突破', '综合应用', '复盘与检验']
    per_week = []
    for week in range(1, weeks + 1):
        stage = stage_names[min(len(stage_names) - 1, (week - 1) * len(stage_names) // weeks)]
        per_week.append(f'第 {week} 周（{stage}）：{hours} 小时，围绕「{goal}」的第 {week}/{weeks} 阶段任务')
        ctx.add_fact(per_week[-1])
    ctx.artifacts.append({
        'type': 'timeline',
        'title': f'{weeks} 周学习路径',
        'items': [{'title': f'第 {week} 周', 'detail': per_week[week - 1]} for week in range(1, weeks + 1)],
    })

    context = ctx.context_block(chunks)
    prompt = f"""【任务】为一名本科学生制定个性化学习路径。
【学生画像】{_profile_line(ctx.profile)}
【学习目标】{goal}
【周期】{weeks} 周，每周可投入 {hours} 小时
【检索资料】
{context}

【输出要求】
## 一、目标拆解（把大目标拆成 3~5 个可检验的子目标）
## 二、每周计划（表格：周次 | 主题 | 任务 | 预计时长 | 检验方式）
## 三、核心资源清单（教材章节 + 平台资料 + 练习，标注引用编号）
## 四、自适应调整规则（若某阶段未达标，如何调整后续计划）
## 五、阶段检验点（每阶段一道自测题或一个小项目）"""
    answer = ctx.ask('generate', '生成学习路径', prompt, task='path', chunks=chunks)
    return ctx.result(answer, {'weeks': weeks})


@register(
    'mistake_review', '错题归因与巩固', 'learn', 'ph-magnifying-glass',
    '判定错误类型（概念/边界/计算/规范），给出归因与同类巩固练习',
    'mistake',
    [
        {'key': 'question', 'label': '题目', 'type': 'textarea', 'required': True},
        {'key': 'student_answer', 'label': '你的答案', 'type': 'textarea', 'required': True},
        {'key': 'correct_answer', 'label': '正确答案（可选）', 'type': 'textarea'},
    ],
    ['错误类型判定', '检索知识盲区', '归因分析', '生成巩固练习'],
    tags=['错题本', '归因', '巩固练习'],
)
def run_mistake_review(ctx):
    question = ctx.input('question')
    student_answer = ctx.input('student_answer')
    correct = ctx.input('correct_answer')
    chunks = ctx.retrieve('retrieve', '检索相关知识点与典型错因', f'{question} 常见错误 易错点', top_k=4)

    signals = []
    if re.search(r'\bO\(', student_answer) and '最坏' not in student_answer:
        signals.append('复杂度结论未区分最好/平均/最坏情况')
    if re.search(r'(<=|>=|while|for)', student_answer) and not re.search(r'(边界|空|len|None)', student_answer):
        signals.append('代码未显式处理边界（空输入/单元素）')
    if len(student_answer) < 30:
        signals.append('作答过于简短，缺少推导过程')
    if correct:
        signals.append(f'与参考答案的关键词重合度：{round(overlap_score(correct, student_answer) * 100, 1)}%')
    for item in signals:
        ctx.add_fact('错因线索：' + item)

    context = ctx.context_block(chunks)
    prompt = f"""【任务】对一道错题做归因分析，并给出巩固方案。
【题目】{question}
【学生答案】{student_answer}
【正确答案】{correct or '（未提供，请依据检索资料判定）'}
【检索资料】
{context}

【输出要求】
## 一、错误类型判定（概念性 / 边界条件 / 计算过程 / 规范表达，给出判定理由）
## 二、归因分析（指出是哪一步认知出了偏差，标注引用编号）
## 三、正确解法（分步演示，附复杂度）
## 四、同类巩固练习（2 道，含答案）
## 五、三句话记忆卡"""
    answer = ctx.ask('generate', '生成归因报告', prompt, task='mistake', chunks=chunks)
    return ctx.result(answer)


@register(
    'quiz', '自测练习生成', 'learn', 'ph-exam',
    '按知识点与难度生成自测题，附答案、解析与考点分布',
    'quiz',
    [
        {'key': 'topic', 'label': '知识点', 'type': 'text', 'required': True, 'placeholder': '例如：二叉树遍历'},
        {'key': 'count', 'label': '题目数量', 'type': 'number', 'default': '5'},
        {'key': 'difficulty', 'label': '难度', 'type': 'select', 'options': ['基础', '中等', '综合'], 'default': '中等'},
        {'key': 'types', 'label': '题型', 'type': 'select', 'options': ['混合', '选择题', '填空简答', '编程题'], 'default': '混合'},
    ],
    ['检索知识点', '生成题目与答案', '校验难度与考点覆盖'],
    tags=['自测', '题目生成', '考点覆盖'],
)
def run_quiz(ctx):
    topic = ctx.input('topic')
    count = max(1, min(int(ctx.input('count', '5') or 5), 20))
    difficulty = ctx.input('difficulty', '中等')
    types = ctx.input('types', '混合')
    chunks = ctx.retrieve('retrieve', '检索知识点与考点', f'{topic} 考点 定义 性质', top_k=5)
    context = ctx.context_block(chunks)
    prompt = f"""【任务】围绕「{topic}」生成 {count} 道{difficulty}难度、题型为{types}的自测题。
【检索资料】
{context}

【输出要求】每题严格按以下格式：
**第 N 题（题型 / 难度）** 题干
- A. ...
- B. ...
- C. ...
- D. ...
<details>参考答案与解析（含引用编号 [n]）</details>

最后输出「考点分布表」（知识点 | 题号 | 分值）与「作答建议」。"""
    answer = ctx.ask('generate', '生成自测题', prompt, task='quiz', chunks=chunks)
    return ctx.result(answer)


@register(
    'complex_solver', '复杂问题求解', 'learn', 'ph-puzzle-piece',
    '拆解开放性综合问题，给出科学方法指引并拓展思维边界',
    'generic',
    [
        {'key': 'question', 'label': '综合性问题', 'type': 'textarea', 'required': True,
         'placeholder': '例如：设计一个支持千万级用户的排行榜系统，要求实时更新'},
    ],
    ['问题理解与边界界定', '任务拆解', '检索方法学依据', '分层求解', '思维拓展'],
    tags=['复杂问题', '拆解', '方法论'],
)
def run_complex_solver(ctx):
    question = ctx.input('question')
    parts = [segment.strip() for segment in re.split(r'[，,；;。\n]', question) if len(segment.strip()) >= 4]
    if len(parts) >= 3:
        ctx.note('decompose', '问题拆解', f'识别到 {len(parts)} 个子问题，进入多阶段求解', kind='branch')
    else:
        ctx.note('decompose', '问题拆解', '问题较聚焦，采用「单主线 + 双校验」策略', kind='branch')
    for index, part in enumerate(parts[:5], start=1):
        ctx.add_fact(f'子问题 {index}：{clip(part, 60)}')

    chunks = ctx.retrieve('retrieve', '检索方法学与工程依据', question, top_k=6)
    if len(parts) >= 3:
        ctx.note('branch', '条件分支', '子问题数量 ≥ 3，启用「逐项求解 + 汇总权衡」分支', kind='branch')

    context = ctx.context_block(chunks)
    prompt = f"""【任务】回答一个开放性、综合性问题，重点展示「如何思考」而不只是结论。
【问题】{question}
【检索资料】
{context}

【输出要求】
## 一、问题界定（关键约束、隐含条件、成功标准）
## 二、任务拆解（表格：子问题 | 依赖关系 | 判定标准）
## 三、分层求解（逐个子问题给出方案与依据，标注引用编号）
## 四、方案权衡（时间/空间/复杂度/工程成本三维对比表）
## 五、可能的陷阱（3 条，说明触发条件）
## 六、思维拓展（2 个反问，引导学生自主探索）
## 七、可执行的第一步"""
    answer = ctx.ask('generate', '分层求解', prompt, task='complex', chunks=chunks)
    return ctx.result(answer)


# ==================== 助研场景 ====================
@register(
    'frontier', '领域前沿追踪', 'research', 'ph-radar',
    '检索并归纳研究方向，生成热点图谱与趋势判断',
    'frontier',
    [
        {'key': 'topic', 'label': '研究主题', 'type': 'text', 'required': True, 'placeholder': '例如：检索增强生成在教育中的应用'},
        {'key': 'window', 'label': '时间窗口', 'type': 'select', 'options': ['近1年', '近3年', '近5年'], 'default': '近3年'},
    ],
    ['检索论文与教材', '主题聚类归纳', '关键词热点图谱', '趋势与空白判断', '精读清单'],
    tags=['文献追踪', '热点图谱', '研究空白'],
)
def run_frontier(ctx):
    topic = ctx.input('topic')
    window = ctx.input('window', '近3年')
    chunks = ctx.retrieve('retrieve', '检索论文与研究资料', topic, top_k=6, prefer=('paper', 'textbook'))
    corpus = ' '.join(chunk['text'] for chunk in chunks)
    word_cloud = keywords(corpus, top_n=12)
    if word_cloud:
        ctx.add_fact('高频主题词：' + '、'.join(word_cloud))
        ctx.artifacts.append({
            'type': 'tags',
            'title': '热点关键词',
            'items': [{'label': word, 'weight': max(1, 8 - index)} for index, word in enumerate(word_cloud)],
        })
    paper_count = sum(1 for chunk in chunks if chunk['source_type'] == 'paper')
    ctx.add_fact(f'本次检索命中 {len(chunks)} 个知识块，其中学术论文类 {paper_count} 条（时间窗口：{window}）')

    context = ctx.context_block(chunks)
    prompt = f"""【任务】做一次「{topic}」的领域前沿追踪，时间窗口 {window}。
【检索资料】
{context}

【输出要求】
## 一、研究主题归纳（3~5 个主题，每个一句话概括 + 代表工作，标注引用编号）
## 二、热点图谱（用 Markdown 表格呈现「主题 | 关联关键词 | 热度判断」）
## 三、发展趋势（3 条，说明判断依据）
## 四、研究空白与机会（2~3 条，说明为什么是空白）
## 五、精读清单（按优先级排序，注明读什么、为什么读）
## 六、可复现的最小实验建议（1 条）"""
    answer = ctx.ask('generate', '生成前沿追踪报告', prompt, task='frontier', chunks=chunks)
    return ctx.result(answer, {'keywords': word_cloud})


@register(
    'writing', '学术写作辅助', 'research', 'ph-pencil-simple-line',
    '文献综述框架、论文结构搭建、语言润色与 GB/T 7714 格式检查',
    'writing',
    [
        {'key': 'topic', 'label': '写作主题', 'type': 'text', 'required': True},
        {'key': 'stage', 'label': '写作环节', 'type': 'select', 'options': ['框架搭建', '文献综述', '语言润色', '格式规范检查'], 'default': '框架搭建'},
        {'key': 'draft', 'label': '已有草稿（可选）', 'type': 'textarea', 'placeholder': '粘贴需要润色或检查的段落'},
    ],
    ['学术诚信检查', '检索文献支撑', '结构化产出', '引用格式校验'],
    tags=['论文框架', '综述', '润色', 'GB/T 7714'],
)
def run_writing(ctx):
    topic = ctx.input('topic')
    stage = ctx.input('stage', '框架搭建')
    draft = ctx.input('draft')
    guard = check_academic_integrity((topic or '') + ' ' + (draft or '') + ' ' + ctx.all_text())
    if guard['blocked']:
        ctx.note('guard', '学术诚信检查', f"命中风险：{guard['reason']}", kind='guard', status='blocked')
        return ctx.result(
            '### 暂不受理该写作请求\n\n'
            f'请求命中学术诚信红线：**{guard["reason"]}**。\n\n'
            '平台只提供「框架、结构、语言、格式」层面的写作支持，不生成虚构文献或伪造数据。'
            '如需引用，请提供真实可核验的文献信息，我可以帮你按 GB/T 7714 规范化著录。',
            {'blocked': True, 'block_reason': guard['reason']},
        )
    ctx.note('guard', '学术诚信检查', '通过（仅提供结构化写作支持，不代写主体论证）', kind='guard')

    chunks = ctx.retrieve('retrieve', '检索文献支撑', topic, top_k=5, prefer=('paper', 'textbook'))
    context = ctx.context_block(chunks)
    format_rules = ''
    if stage == '格式规范检查':
        format_rules = ('\n【格式要求】按 GB/T 7714—2015 著录：专著 [M]、期刊 [J]、会议 [C]、学位论文 [D]、'
                        '标准 [S]、电子资源 [EB/OL]；正文引用用上标序号，与文末参考文献一一对应。')
    prompt = f"""【任务】为「{topic}」提供{stage}支持。
【已有草稿】{draft or '（无）'}
【检索资料】
{context}
{format_rules}

【输出要求】
## 一、{stage}产出（按环节给具体内容：框架给章节树；综述给主题式脉络；润色给修改前后对照表；格式检查给问题清单）
## 二、论证要点（3~5 条，说明每条需要什么证据支撑，标注引用编号）
## 三、引用与规范检查（列出需补充或修正的引用位置）
## 四、写作风险提示（如论证跳跃、概念混用、数据来源不明）
## 五、下一步建议（可执行）"""
    answer = ctx.ask('generate', f'生成{stage}支持', prompt, task='writing', chunks=chunks)
    return ctx.result(answer)


@register(
    'data_analysis', '科研数据分析', 'research', 'ph-chart-bar',
    '解析实验/调查表格，做描述统计并给出可视化与结论建议',
    'data_analysis',
    [
        {'key': 'attachment_name', 'label': '数据文件', 'type': 'file', 'required': True,
         'placeholder': '上传 csv / xlsx 文件'},
        {'key': 'analysis_type', 'label': '分析类型', 'type': 'select',
         'options': ['描述统计', '组间对比', '相关性探索'], 'default': '描述统计'},
        {'key': 'context', 'label': '研究背景（可选）', 'type': 'textarea'},
    ],
    ['解析数据文件', '计算描述统计', '生成可视化方案', '结论与局限说明'],
    tags=['数据分析', '可视化', '统计'],
)
def run_data_analysis(ctx):
    import os

    from config import Config as _Config

    name = os.path.basename(ctx.input('attachment_name', ''))
    analysis_type = ctx.input('analysis_type', '描述统计')
    background = ctx.input('context', '')
    if not name:
        ctx.note('parse', '解析数据文件', '未提供数据文件', status='error')
        return ctx.result('请先上传 csv / xlsx 数据文件，再运行科研数据分析工作流。')
    path = os.path.join(_Config.UPLOAD_DIR, name)
    if not os.path.isfile(path):
        ctx.note('parse', '解析数据文件', '文件不存在或已过期，请重新上传', status='error')
        return ctx.result(f'未找到数据文件 `{name}`，请重新上传后再试。')

    try:
        table = _table_stats(path)
    except Exception as error:
        ctx.note('parse', '解析数据文件', f'解析失败：{error}', status='error')
        return ctx.result(f'数据文件解析失败：{error}\n\n请确认文件为 csv / xlsx 且首行为表头。')

    ctx.note('parse', '解析数据文件',
             f"工作表 {table.get('sheet') or '默认'}；{len(table['header'])} 列 × {table['row_count']} 行")
    ctx.add_fact(f"数据规模：{table['row_count']} 行 × {len(table['header'])} 列")
    if not table['stats']:
        ctx.note('stats', '计算描述统计', '未识别到可统计的数值列', status='warn')
    for item in table['stats'][:4]:
        summary = item['summary']
        ctx.add_fact(
            f"{item['column']}：n={summary['count']}，均值 {summary['mean']}，标准差 {summary['std']}，"
            f"中位数 {summary['median']}，范围 [{summary['min']}, {summary['max']}]"
        )
    ctx.artifacts.extend(table['charts'])
    if table['header']:
        preview = {
            'type': 'table',
            'title': '数据预览（前 8 行）',
            'columns': [str(cell) for cell in table['header'][:8]],
            'rows': [[str(cell) for cell in row[:8]] for row in table['rows'][:8]],
        }
        ctx.artifacts.append(preview)

    chunks = ctx.retrieve('retrieve', '检索分析方法规范', f'{analysis_type} 实验数据 统计方法', top_k=3)
    context = ctx.context_block(chunks)
    prompt = f"""【任务】对一份实验/调查数据进行{analysis_type}分析。
【研究背景】{background or '未提供'}
【数据事实】
{chr(10).join('- ' + fact for fact in ctx.facts)}
【检索资料】
{context}

【输出要求】
## 一、数据概览（规模、字段含义推测、缺失情况）
## 二、描述统计解读（结合数值说明分布形态，不要只罗列数字）
## 三、可视化建议（图表类型、坐标轴、要突出的结论）
## 四、初步结论（3 条，注明证据强度）
## 五、统计局限与注意事项（样本量、多重比较、相关≠因果）"""
    answer = ctx.ask('generate', '生成分析结论', prompt, task='data_analysis', chunks=chunks)
    return ctx.result(answer, {'data': {'columns': table['header'][:8], 'rows': table['rows'][:20]}})


# ==================== 对外执行入口 ====================
def run_agent(agent_id, inputs, user=None, profile=None, history=None, session_id=None):
    if agent_id not in AGENTS:
        raise KeyError(f'未知智能体：{agent_id}')
    agent = AGENTS[agent_id]
    ctx = AgentContext(agent_id, inputs, user=user, profile=profile, history=history, session_id=session_id)
    started = time.time()
    payload = agent['run'](ctx)
    payload.setdefault('agents', None)
    payload['latency_ms'] = int((time.time() - started) * 1000)
    payload['inputs'] = {key: value for key, value in (inputs or {}).items() if key != 'files'}
    return payload


def agent_catalog():
    return list_agents()