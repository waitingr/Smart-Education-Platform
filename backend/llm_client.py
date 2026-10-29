"""统一大模型客户端（多提供方 + 优雅降级）。

优先级（LLM_PROVIDER=auto 时）：
  1. local  ：本地 Qwen2.5 等 HuggingFace 因果语言模型（torch 惰性导入）
  2. ollama ：本机 Ollama 视觉大模型（默认 qwen3-vl:8b，可直接读图并生成回答）
  3. spark  ：讯飞星火 OpenAI 兼容 HTTP 接口（赛事推荐平台）
  4. openai ：任意 OpenAI 兼容网关（vLLM / Ollama / DeepSeek / 学校自建）
  5. offline：离线检索式生成引擎（纯标准库），保证无模型、无网络时平台仍可完整演示

「offline」不是假的回复：它把检索到的权威知识原文做抽取式结构化编排并逐句标注引用编号，
在 UI 上明确标记为 degraded（未接入大模型），既诚实又能验证检索、引用、工作流全链路。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from config import Config
from text_utils import (clip, coherence, contrast_penalty, entities_in,
                        entity_affinity, overlap_score, split_sentences,
                        tokenize)

_LOCAL_MODEL_CHOICES = (
    # 当前平台默认走 Ollama 的 qwen3-vl:8b；这里只是「local 后端可选加载」的候选目录名
    ('Qwen3-VL-8B-Instruct', ('qwen--Qwen3-VL-8B-Instruct', 'Qwen3-VL-8B-Instruct')),
    ('Qwen2.5-7B-Instruct', ('qwen--Qwen2.5-7B-Instruct', 'Qwen2.5-7B-Instruct')),
    ('Qwen2.5-1.5B-Instruct', ('qwen--Qwen2.5-1.5B-Instruct', 'Qwen2.5-1.5B-Instruct')),
    ('Qwen2.5-3B-Instruct', ('qwen--Qwen2.5-3B-Instruct', 'Qwen2.5-3B-Instruct')),
)


@dataclass
class LLMResult:
    text: str
    provider: str = 'offline'
    model: str = 'retrieval-composer'
    degraded: bool = True
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    notice: str = ''
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            'provider': self.provider,
            'model': self.model,
            'degraded': self.degraded,
            'latency_ms': self.latency_ms,
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'notice': self.notice,
        }


def _estimate_tokens(text):
    return max(1, len(text or '') // 2)


def _find_local_model():
    if Config.LLM_MODEL_PATH and os.path.isdir(Config.LLM_MODEL_PATH):
        return Config.LLM_MODEL_PATH
    llm_root = os.path.join(Config.LLM_DIR if hasattr(Config, 'LLM_DIR') else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'llm'))
    if not os.path.isdir(llm_root):
        return None
    for _, aliases in _LOCAL_MODEL_CHOICES:
        for alias in aliases:
            for suffix in (os.path.join('snapshots', 'master'), ''):
                candidate = os.path.join(llm_root, alias, suffix) if suffix else os.path.join(llm_root, alias)
                if os.path.isfile(os.path.join(candidate, 'config.json')):
                    return candidate
    return None


class OfflineComposer:
    """抽取式结构化生成：把知识库原文编排成可读、可溯源的教学/科研内容。"""

    HEADER = '> ⓘ 当前为**离线检索模式**（未连接大模型），以下内容由学科知识库原文按工作流结构化编排生成，逐句标注引用编号，可直接用于功能验收。'

    SECTION_PLANS = {
        'qa': [('一句话结论', 'lead'), ('知识原文摘录', 'excerpt'), ('再想一想', 'probe')],
        'tutor': [('一句话结论', 'lead'), ('知识原文摘录', 'excerpt'), ('再想一想', 'probe')],
        'lesson_plan': [('教学目标', 'goal'), ('重点与难点', 'best'), ('教学环节设计', 'ordered'), ('课堂活动与提问', 'probe'), ('作业与评价', 'advice')],
        'grading': [('评分要点', 'ordered'), ('逐项判定', 'best'), ('改进建议', 'advice')],
        'diagnosis': [('数据概览', 'facts'), ('薄弱知识点', 'weak'), ('教学干预建议', 'actions')],
        'material': [('内容要点', 'best'), ('结构大纲', 'ordered'), ('配套练习建议', 'probe')],
        'path': [('学习目标拆解', 'goal'), ('阶段任务', 'ordered'), ('资源与检验', 'advice')],
        'mistake': [('错误类型判定', 'best'), ('归因分析', 'ordered'), ('巩固练习', 'probe')],
        'quiz': [('考点分布', 'best'), ('练习题目', 'ordered'), ('讲评要点', 'advice')],
        'frontier': [('研究主题归纳', 'best'), ('热点与发展趋势', 'keywords'), ('值得精读的文献', 'refs')],
        'writing': [('写作框架', 'ordered'), ('论证要点', 'best'), ('规范性检查', 'advice')],
        'data_analysis': [('数据概览', 'facts'), ('统计分析', 'best'), ('可视化建议', 'advice')],
        'complex': [('问题界定', 'best'), ('任务拆解', 'ordered'), ('可能踩坑', 'probe'), ('可执行的第一步', 'advice')],
        'generic': [('要点', 'best'), ('展开说明', 'ordered')],
    }

    def compose(self, question, chunks, task='generic', facts=None, style=None, user_prompt=''):
        self._current_chunks = list(chunks or [])
        self._current_question = question or ''
        sections = self.SECTION_PLANS.get(task) or self.SECTION_PLANS['generic']
        evidence = self._evidence(question or user_prompt, chunks)
        facts = [str(item) for item in (facts or []) if str(item).strip()]
        level = (style or {}).get('level', 'intermediate')
        depth = {'beginner': 3, 'intermediate': 4, 'advanced': 5}.get(level, 4)

        lines = [self.HEADER, '']
        rendered_kinds = {kind for _, kind in sections}
        # 方案里已经有「数据概览/薄弱知识点」段落时，不再重复罗列一遍原始事实
        if facts and not ({'facts', 'weak'} & rendered_kinds):
            lines.append('### 工作流计算事实')
            for fact in facts:
                lines.append(f'- {fact}')
            lines.append('')

        used = set()
        for title, kind in sections:
            body = self._render(kind, title, evidence, facts, depth, used)
            if body:
                lines.append(f'### {title}')
                lines.extend(body)
                lines.append('')

        if not evidence and not facts:
            lines.append('知识库中暂未检索到与该问题直接相关的内容。建议：')
            lines.append('- 换用学科术语重新表述（例如使用「时间复杂度」「二叉排序树」等标准术语）；')
            lines.append('- 或在「知识库管理」中补充该主题的教材章节后再试。')
            lines.append('')
        return '\n'.join(lines).strip()

    # ---------------- 内部实现 ----------------
    @staticmethod
    def _clean(sentence):
        """把原文片段整理成可直接阅读的句子：去掉列表序号、加粗标记、表格竖线。"""
        text = (sentence or '').strip()
        if text.startswith('|'):
            cells = [cell.strip().strip('*`') for cell in text.strip('|').split('|')]
            cells = [cell for cell in cells if cell and not set(cell) <= {'-', ':'}]
            if len(cells) >= 2:
                return f'{cells[0]}：{"，".join(cells[1:])}'
            return '，'.join(cells)
        text = re.sub(r'^[\s>\-*·•]+', '', text)
        text = re.sub(r'^\(?\d{1,2}[).、．]\s*', '', text)
        text = re.sub(r'^（\d{1,2}）', '', text)
        text = text.replace('**', '').replace('`', '')
        text = re.sub(r'\s+', ' ', text)
        return text.strip(' ；;，,')

    @staticmethod
    def _readable(text):
        return len(text) >= 14 and not re.fullmatch(r'[\W_]+', text)

    def _evidence(self, question, chunks, limit=14):
        asked_entities = entities_in(question)
        asks_reason = bool(re.search(r'为什么|为何|原因|怎么会|凭什么', question or ''))
        asks_steps = bool(re.search(r'如何|怎么|怎样|步骤|流程|实现', question or ''))
        scored = []
        for chunk in chunks or []:
            chunk_text = chunk.get('text') or chunk.get('content') or ''
            # 「定义：」后紧跟的列表项其实是同一条定义，合并后再切句，避免定义与说明被拆散
            chunk_text = re.sub(r'：\s*\n\s*[-*•]\s*', '：', chunk_text)
            chunk_score = float(chunk.get('score') or 0.0)
            chunk_overlap = overlap_score(question, chunk_text)
            for sentence in split_sentences(chunk_text):
                cleaned = self._clean(sentence)
                if not self._readable(cleaned):
                    continue
                topical = coherence(question, cleaned)
                rank = int(chunk.get('index') or 1)
                score = (1.0 * overlap_score(question, cleaned)
                         + 0.8 * topical
                         + 0.2 * chunk_overlap
                         + 0.1 * chunk_score
                         - 0.06 * max(0, rank - 1)   # 检索名次先验：越靠前的块越可能是对题的
                         - contrast_penalty(question, cleaned)
                         + entity_affinity(question, cleaned))
                # 答题意图匹配：问「为什么」要的是因果句，问「怎么做」要的是步骤句
                if asks_reason and re.search(r'因为|原因|由于|否则|才能|必须|应当|使得|导致|含义|状态', cleaned):
                    score += 0.15
                if asks_steps and re.search(r'首先|第一步|步骤|先|然后|最后|过程', cleaned):
                    score += 0.12
                # 附件（含图片识别结果）是用户本轮真正在问的对象，结论必须先从它里面取
                if chunk.get('source_type') == 'attachment':
                    score += 0.85
                # 长度先验：过短的碎片通常只是列表项，30-90 字的句子更可能承载完整解释
                if 30 <= len(cleaned) <= 90:
                    score += 0.12
                elif len(cleaned) < 15:
                    score -= 0.05
                # 反面示例句（「认为……」「误区」）标记降级：优先引用正面表述，无替代时才使用
                negative = bool(re.search(r'^(认为|误以为|不要|禁止|不可|避免|混淆|误用)|误区|错误做法', cleaned))
                if negative:
                    score -= 0.12
                # 整节都在讲「常见错误/易错点」时，句子默认是反面表述，结论段应让位给正面章节
                elif re.search(r'常见错误|易错|误区|踩坑|典型错', str(chunk.get('section') or '')):
                    negative = True
                    score -= 0.18
                scored.append((score, cleaned, int(chunk.get('index') or 0), negative, topical,
                               entities_in(cleaned)))
        scored.sort(key=lambda item: item[0], reverse=True)

        # 主题连贯度过滤：优先只保留与问题共享 3 元组的句子，避免同章节无关句混入
        coherent_items = [item for item in scored if item[4] > 0]
        pool = coherent_items if len(coherent_items) >= 5 else scored

        results, seen = [], set()
        for score, cleaned, index, negative, _, sentence_entities in pool:
            fingerprint = re.sub(r'\W', '', cleaned)[:20]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            # 问题点名了某个实体，而这句话讲的是别的同类实体（问二叉排序树却引用哈希表）→ 标记为跑题
            miss = bool(asked_entities) and bool(sentence_entities - asked_entities) \
                and not (sentence_entities & asked_entities)
            results.append({
                'score': score, 'sentence': cleaned, 'index': index,
                'negative': negative, 'miss': miss,
            })
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _cite(item):
        return f"{item['sentence']} [{item['index']}]"

    def _best_excerpt_chunk(self, evidence):
        """摘录应取「与问题最贴合」的原文块，而不是机械取检索第一名。

        检索排序受长度、词频、权威度等多因素影响，排在第一的块未必是讲这个问题的那一段；
        这里用该块内最相关句子的得分重新挑选，保证摘录内容对题。
        """
        chunks = getattr(self, '_current_chunks', []) or []
        if len(chunks) <= 1:
            return chunks[0] if chunks else {}
        question = getattr(self, '_current_question', '') or ''
        best_by_index = {}
        for item in evidence or []:
            index = int(item.get('index') or 0)
            if index:
                best_by_index[index] = max(best_by_index.get(index, 0.0), float(item.get('score') or 0.0))
        ranked = []
        for position, chunk in enumerate(chunks):
            rank = int(chunk.get('index') or 0) or (position + 1)
            score = best_by_index.get(rank)
            if score is None:
                score = overlap_score(question, chunk.get('text') or '')
            # 排序名次作为微弱先验，避免同分时任取一块
            ranked.append((float(score), -rank, chunk))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return ranked[0][2]

    def _render(self, kind, title, evidence, facts, depth, used):
        def take(count, ordered=False):
            pool = sorted(evidence, key=lambda item: (bool(item.get('negative')), item['index'] if ordered else 0))
            if not ordered:
                # 排序优先级：正面表述 → 未跑题 → 分数高
                pool = sorted(evidence, key=lambda item: (bool(item.get('negative')),
                                                          bool(item.get('miss')),
                                                          -item['score']))
            picked = []
            for item in pool:
                if item['sentence'] in used:
                    continue
                used.add(item['sentence'])
                picked.append(item)
                if len(picked) >= count:
                    break
            return picked

        if kind == 'facts':
            detail = [fact for fact in facts if not fact.startswith(('薄弱知识点', '掌握较好'))] if facts else []
            return [f'- {fact} [数据来源：平台学习行为库]' for fact in detail] if detail else []

        if kind == 'weak':
            detail = [fact for fact in facts if fact.startswith(('薄弱知识点', '掌握较好'))] if facts else []
            return [f'- {fact} [依据：作业成绩聚合统计]' for fact in detail] if detail else []

        if kind == 'lead':
            picked = take(2)
            question = getattr(self, '_current_question', '') or ''
            # 问「为什么」时，结论里必须出现一句真正给原因的话，而不是只给定义
            if re.search(r'为什么|为何|原因|怎么会', question):
                causal = re.compile(r'因为|原因|由于|否则|才能|必须|应当|使得|导致|含义|状态')
                if not any(causal.search(item['sentence']) for item in picked):
                    extra = [
                        item for item in evidence
                        if causal.search(item['sentence']) and item['sentence'] not in used
                    ]
                    if extra:
                        if len(picked) >= 2:
                            used.discard(picked[-1]['sentence'])
                            picked[-1] = extra[0]
                        else:
                            picked.append(extra[0])
                        used.add(extra[0]['sentence'])
            return [self._cite(item) for item in picked]

        if kind == 'excerpt':
            if not getattr(self, '_current_chunks', None):
                return []
            chunk = self._best_excerpt_chunk(evidence)
            raw_lines = []
            for line in (chunk.get('text') or '').split('\n'):
                line = line.strip()
                if not line or set(line) <= {'-', ':', '|', ' '}:
                    continue
                if line.startswith('|'):
                    cells = [cell.strip() for cell in line.strip('|').split('|')]
                    cells = [cell for cell in cells if cell and not set(cell) <= {'-', ':'}]
                    line = '：'.join(cells[:2]) + (f'，{"，".join(cells[2:])}' if len(cells) > 2 else '')
                raw_lines.append(line.replace('**', '').replace('`', ''))
            body = clip(' / '.join(raw_lines), 520)
            body = re.sub(r'\s*/\s*[-*•]\s*', '；', body)
            body = re.sub(r'(^|；)\s*/\s*', r'\1', body)
            if not body:
                return []
            return [f'> {body} [{chunk.get("index")}]', '',
                    f'出处：{chunk.get("locator") or chunk.get("doc_title")}']

        if kind == 'best':
            picked = take(depth)
            return [f'- {self._cite(item)}' for item in picked]

        if kind == 'ordered':
            picked = take(depth + 1, ordered=True)
            return [f'{position}. {self._cite(item)}' for position, item in enumerate(picked, start=1)]

        if kind == 'contrast':
            picked = take(2)
            if not picked:
                return []
            return [
                '| 对比维度 | 说明 |',
                '| --- | --- |',
                f'| 定义与边界 | {clip(picked[0]["sentence"], 90)} [{picked[0]["index"]}] |',
                f'| 常见误区 | {(clip(picked[1]["sentence"], 90) + f" [{picked[1]['index']}]") if len(picked) > 1 else "建议结合教材对应章节的例题进一步辨析"} |',
            ]

        if kind == 'probe':
            candidates = [item for item in take(6) if item['score'] > 0.22]
            # 只引用完整、可独立成句的原文；被截断的半句话拿来做追问会很别扭
            quotable = [
                item for item in candidates
                if 10 <= len(item['sentence']) <= 48
                and not item['sentence'].endswith(('：', ':', '，', '、', '（', '('))
            ][:2]
            lines = ['建议带着以下问题回顾本节内容：']
            for item in quotable:
                lines.append(f'- 原文说「{item["sentence"].rstrip("。；;")}」[{item["index"]}]，这个结论的边界条件是什么？')
            generic = [
                '- 这一结论在空输入、单元素、极端数据下是否仍然成立？',
                '- 如果去掉某个前提条件（如"边权非负""关键字互异"），结论会怎样变化？',
                '- 能否用一个反例说明它不适用的场景？',
            ]
            for text_line in generic[: 3 - len(quotable)]:
                lines.append(text_line)
            return lines

        if kind == 'goal':
            picked = take(2)
            goals = []
            if picked:
                goals.append(f'- 掌握：{clip(picked[0]["sentence"], 120)} [{picked[0]["index"]}]')
            if len(picked) > 1:
                goals.append(f'- 理解：{clip(picked[1]["sentence"], 120)} [{picked[1]["index"]}]')
            goals.append('- 应用：能在真实题目/代码中识别并正确使用上述概念')
            return goals

        if kind == 'actions':
            detail = [fact for fact in facts if fact.startswith('薄弱知识点')] if facts else []
            tactics = (
                '安排一次 20 分钟专题复盘，并布置 1 组同类题；不及格学生课后单独辅导，下次课前用 5 分钟小测验证',
                '整理 3 道典型错题让学生在课上互评，教师只点出评分依据与易错边界',
                '给出一道带陷阱的变式题，先独立作答再对照教材原文自查，最后总结适用条件',
            )
            lines = []
            for position, fact in enumerate(detail[:3]):
                topic = fact.split('：', 1)[-1].split('｜')[0]
                lines.append(f'{position + 1}. 针对「{topic}」{tactics[position % len(tactics)]}。')
            lines.append(f'{len(lines) + 1}. 分层布置作业：达标学生做迁移题，未达标学生先做课本基础题。')
            lines.append(f'{len(lines) + 1}. 一周后回看本页诊断指标，对比平均分与不及格率的变化。')
            return lines

        if kind == 'advice':
            picked = take(2)
            lines = ['- 建议先用 1 道基础题验证概念理解，再用 1 道综合题检验迁移能力。']
            if picked:
                lines.append(f'- 重点关注：{clip(picked[0]["sentence"], 110)} [{picked[0]["index"]}]')
            lines.append('- 完成后可在「学情诊断」中查看该知识点的掌握度变化。')
            return lines

        if kind == 'keywords':
            words = []
            for item in evidence[:8]:
                for word in tokenize(item['sentence'])[:2]:
                    if len(word) == 2 and word not in words:
                        words.append(word)
            return [f'- 关键词：{"、".join(words[:8]) or "暂无"}'] if words else []

        if kind == 'refs':
            seen, refs = set(), []
            for item in evidence[:6]:
                if item['index'] in seen:
                    continue
                seen.add(item['index'])
                refs.append(f'- [{item["index"]}] {clip(item["sentence"], 110)}')
            return refs

        return []


class LLMClient:
    def __init__(self):
        self.provider = None
        self.model_name = ''
        self.notice = ''
        self._model = None
        self._tokenizer = None
        self._load_error = None
        self._composer = OfflineComposer()
        self._resolve_provider()

    # ---------------- 能力探测 ----------------
    def supports_images(self):
        """当前生成模型能否直接"看图"（图片以 image_url 送入 chat 接口）。"""
        requested = Config.LLM_VISION
        if requested in ('0', 'false', 'off', 'no'):
            return False
        if requested in ('1', 'true', 'on', 'yes'):
            return True
        if self.provider in ('local', 'offline'):
            return False
        if self.provider == 'ollama':
            return True  # 本机 Ollama 走的是视觉模型
        name = (self.model_name or '').lower()
        return any(tag in name for tag in ('vl', 'vision', 'omni', '4v', '4o', 'gpt-5'))

    # ---------------- 提供方解析 ----------------
    def _local_ready(self):
        """本地模型可用的前提是：有完整权重 + 推理依赖可导入。"""
        if not _find_local_model():
            return False
        try:
            import importlib.util
            return all(importlib.util.find_spec(name) is not None
                       for name in ('torch', 'transformers'))
        except (ImportError, ValueError):
            return False

    def _resolve_provider(self):
        requested = Config.LLM_PROVIDER
        if requested == 'ollama':
            if _ollama_ready():
                self.provider, self.model_name = 'ollama', Config.LLM_MODEL_NAME
                return
            self.notice = '未连接到本机 Ollama（请先运行 ollama serve，并确保视觉模型已就绪）'
        if requested in ('local', 'auto'):
            if self._local_ready():
                path = _find_local_model()
                self.provider, self.model_name = 'local', os.path.basename(path.rstrip('\\/'))
                return
            if not _ollama_ready():
                self.notice = '未检测到可用的本地推理环境（需 torch + transformers + 完整模型权重）'
        if requested in ('ollama', 'auto') and _ollama_ready():
            self.provider, self.model_name = 'ollama', Config.LLM_MODEL_NAME
            return
        if requested in ('spark', 'auto') and Config.SPARK_API_KEY:
            self.provider, self.model_name = 'spark', Config.SPARK_MODEL
            return
        if requested in ('openai', 'spark', 'auto') and Config.LLM_BASE_URL and Config.LLM_API_KEY:
            self.provider, self.model_name = 'openai', Config.LLM_MODEL_NAME
            return
        self.provider, self.model_name = 'offline', 'retrieval-composer'
        if not self.notice:
            self.notice = '未检测到可用的大模型服务（本机 Ollama / 本地权重 / 星火 API / OpenAI 兼容网关），已启用离线检索模式'

    # ---------------- 本地模型 ----------------
    def _ensure_local(self):
        if self._model is not None:
            return True
        if self._load_error:
            return False
        path = _find_local_model()
        if not path:
            self._load_error = '本地模型路径不存在'
            return False
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as error:
            self._load_error = f'缺少推理依赖（torch/transformers）：{error}'
            return False
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
            kwargs = {'trust_remote_code': True, 'low_cpu_mem_usage': True}
            quantized = False
            if torch.cuda.is_available():
                kwargs['device_map'] = 'auto'
                kwargs['torch_dtype'] = torch.float16
                try:
                    from transformers import BitsAndBytesConfig
                    kwargs['quantization_config'] = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=torch.float16,
                        bnb_4bit_use_double_quant=True,
                        bnb_4bit_quant_type='nf4',
                    )
                    quantized = True
                except Exception:
                    pass
            else:
                kwargs['device_map'] = 'cpu'
                kwargs['torch_dtype'] = torch.float32
            self._model = AutoModelForCausalLM.from_pretrained(path, **kwargs)
            if not kwargs.get('device_map'):
                self._model = self._model.to('cpu')
            self._model.eval()
            self.notice = f'本地模型已加载（{"4bit" if quantized else "fp32"}）'
            return True
        except Exception as error:  # pragma: no cover - 取决于机器环境
            self._load_error = f'本地模型加载失败：{error}'
            return False

    def _chat_local(self, messages, temperature, max_tokens):
        if not self._ensure_local():
            raise RuntimeError(self._load_error or '本地模型不可用')
        import torch
        prompt = self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self._tokenizer(prompt, return_tensors='pt')
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=max(temperature, 0.01),
                do_sample=temperature > 0.01,
                top_p=Config.LLM_TOP_P,
                repetition_penalty=1.08,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated = output[0][inputs['input_ids'].shape[-1]:]
        return self._tokenizer.decode(generated, skip_special_tokens=True).strip()

    # ---------------- 远程接口 ----------------
    def _chat_http(self, messages, temperature, max_tokens):
        if self.provider == 'spark':
            url = 'https://spark-api-open.xf-yun.com/v1/chat/completions'
            headers = {'Authorization': f'Bearer {Config.SPARK_API_KEY}'}
            model = Config.SPARK_MODEL
        elif self.provider == 'ollama':
            url = f'{Config.VISION_BASE_URL}/v1/chat/completions'
            headers = {'Authorization': 'Bearer ollama'}
            model = Config.LLM_MODEL_NAME
        else:
            url = f'{Config.LLM_BASE_URL}/chat/completions'
            headers = {'Authorization': f'Bearer {Config.LLM_API_KEY}'}
            model = Config.LLM_MODEL_NAME
        payload = json.dumps({
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'stream': False,
        }).encode('utf-8')
        request = urllib.request.Request(url, data=payload, headers={
            'Content-Type': 'application/json; charset=utf-8',
            **headers,
        }, method='POST')
        last_error = None
        for _ in range(2):
            try:
                with urllib.request.urlopen(request, timeout=Config.LLM_TIMEOUT) as response:
                    data = json.loads(response.read().decode('utf-8'))
                choices = data.get('choices') or []
                if not choices:
                    raise RuntimeError(f'接口返回异常：{clip(str(data), 200)}')
                return (choices[0].get('message') or {}).get('content', '') or ''
            except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as error:
                last_error = error
                time.sleep(1.0)
        raise RuntimeError(f'调用大模型接口失败：{last_error}')

    # ---------------- 对外接口 ----------------
    def complete(self, *, system='', user='', question='', chunks=None, task='generic',
                 facts=None, style=None, temperature=None, max_tokens=None, images=None):
        started = time.time()
        temperature = Config.LLM_TEMPERATURE if temperature is None else temperature
        max_tokens = Config.LLM_MAX_NEW_TOKENS if max_tokens is None else max_tokens
        question = question or user
        images = [item for item in (images or []) if item]

        if self.provider in ('local', 'spark', 'openai', 'ollama'):
            messages = []
            if system:
                messages.append({'role': 'system', 'content': system})
            if images and self.supports_images():
                content = [{'type': 'text', 'text': user}]
                for data_url in images:
                    content.append({'type': 'image_url', 'image_url': {'url': data_url}})
                messages.append({'role': 'user', 'content': content})
            else:
                messages.append({'role': 'user', 'content': user})
            try:
                if self.provider == 'local':
                    text = self._chat_local(messages, temperature, max_tokens)
                else:
                    text = self._chat_http(messages, temperature, max_tokens)
                if text.strip():
                    return LLMResult(
                        text=text.strip(),
                        provider=self.provider,
                        model=self.model_name,
                        degraded=False,
                        latency_ms=int((time.time() - started) * 1000),
                        prompt_tokens=_estimate_tokens(system + user),
                        completion_tokens=_estimate_tokens(text),
                        notice=self.notice,
                    )
                raise RuntimeError('模型返回空内容')
            except Exception as error:
                self.notice = f'大模型调用失败（{clip(str(error), 120)}），本次已自动降级为离线检索模式'
                # 本地依赖缺失属于不可恢复的配置问题，永久降级，避免每次请求都重试加载
                if self.provider == 'local' and ('缺少推理依赖' in str(error) or '本地模型' in str(error)):
                    self.provider, self.model_name = 'offline', 'retrieval-composer'
                self._fallback_error = str(error)

        text = self._composer.compose(question, chunks, task=task, facts=facts, style=style, user_prompt=user)
        return LLMResult(
            text=text,
            provider='offline',
            model='retrieval-composer',
            degraded=True,
            latency_ms=int((time.time() - started) * 1000),
            prompt_tokens=_estimate_tokens(system + user),
            completion_tokens=_estimate_tokens(text),
            notice=self.notice or OfflineComposer.HEADER,
        )

    def health(self):
        available = self.provider in ('local', 'spark', 'openai', 'ollama')
        return {
            'provider': self.provider,
            'model': self.model_name,
            'available': available,
            'degraded': not available,
            'vision': self.supports_images(),
            'notice': self.notice,
            'load_error': self._load_error,
        }


def _ollama_ready():
    """本机 Ollama 是否可用（复用视觉服务的探测结果，带 60 秒缓存）。"""
    try:
        from vision_service import get_vision
        return bool(get_vision().available)
    except Exception:
        return False


_client = None


def get_llm():
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm():
    global _client
    _client = None