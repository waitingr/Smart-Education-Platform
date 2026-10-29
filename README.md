# 灵眸智教 · 学科垂类大模型与创新应用平台

> 参赛赛题：**XH-202620 面向一流学科建设的学科垂类大模型与创新应用开发**
> 学科方向：计算机科学与技术（课程载体：《数据结构与算法》）
> 覆盖场景：**助学 / 助教 / 助研** 三类真实教学科研场景，12 个可编排智能体

灵眸智教不是"套壳聊天框"，而是一套**以学科知识库为事实底座、以可编排工作流为执行骨架、以引用溯源为质量约束**的垂类大模型应用平台：
每一条回答都能点开看到"出自哪本教材的哪一节第几页"，每一个场景都由可复现的多步骤工作流驱动，任何 AI 生成的结论都带显式标识。

---

## 1. 三分钟跑起来

### 1.1 环境要求

| 项目 | 最低要求 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 已在 3.14 上验证 |
| 数据库 | 无需安装 | 有 MySQL 就用 MySQL，没有自动降级到本地 SQLite |
| GPU / 大模型 | 无需 | 未接入大模型时自动进入**离线检索模式**，全部功能仍可用 |
| 视觉模型（随项目附带） | 8 GB 显存 | 生成 + 看图共用同一个模型 `qwen3-vl:8b`（5.38 GB），运行时与权重都在 `models/ollama/`；RTX 4060 8GB 实测约 27 秒/张 |
| 内存 | 4 GB | 离线模式约 300 MB |
| 磁盘 | 约 7 GB | 代码/数据 13 MB + Ollama 运行时 1.2 GB + 模型权重 5.4 GB |

### 1.2 安装与启动（Windows PowerShell）

```powershell
cd E:\SmartEduPlatform

# 1) 创建虚拟环境并安装依赖（不含 torch，约 1 分钟）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) 生成演示数据：教师 + 55 名学生 + 4 次作业 + 196 份提交 + 使用记录
.\.venv\Scripts\python.exe scripts\seed_demo.py

# 3) 一键启动（自动拉起项目内的 Ollama、检查模型、启动后端）
.\run.ps1
#   等价于： cd backend; ..\.venv\Scripts\python.exe app.py
```

浏览器打开 **http://127.0.0.1:5000/** 即可（前端由 Flask 同源托管，无需另起服务）。
`.\run.ps1 -Check` 只做环境自检不启动；`.\run.ps1 -Seed` 会先重灌演示数据再启动。

**模型就在项目里，不需要另装 Ollama**：

```text
models\ollama\runtime\ollama.exe   # Ollama 0.40.12 本体（含 CUDA / Vulkan 后端）
models\ollama\store\               # OLLAMA_MODELS：qwen3-vl:8b 权重
```

`run.ps1` 会自动设好 `OLLAMA_HOST` / `OLLAMA_MODELS` 并拉起 `serve`。
单独操作模型用项目自带的入口（它会带上正确的环境变量）：

```powershell
models\ollama\ollama.cmd list      # 看模型
models\ollama\ollama.cmd ps        # 看是否驻留显存
models\ollama\ollama.cmd serve     # 只起推理服务
```

> 不要直接双击 `runtime\ollama.exe`：它会去 `%USERPROFILE%\.ollama\models` 找模型，
> 而本项目的模型在 `store\` 里，表现为"模型明明在这儿却提示没有"。详见 [models/README.md](models/README.md)。

如果要用别的 OpenAI 兼容视觉服务：设 `VISION_PROVIDER=openai` + `VISION_BASE_URL` + `VISION_MODEL` + `VISION_API_KEY`。
未配置时图片附件会明确提示"未配置视觉模型"，不会假装识别成功。

### 1.3 演示账号

| 角色 | 账号 | 密码 | 可见功能 |
| --- | --- | --- | --- |
| 教师 | `teacher@smartedu.cn` | `Teacher@123` | 助学 + 助教 + 助研 + 知识库管理 + 数据看板 |
| 教师（助教） | `assistant@smartedu.cn` | `Teacher@123` | 同上 |
| 学生 | `student2023010101@smartedu.cn` | `Student@123` | 助学 + 助研 |
| 游客 | 直接访问，无需登录 | — | 智能问答、学习/科研工作台、看板 |

### 1.4 一键自检

```powershell
# 36 项端到端检查：健康检查、鉴权、知识库、三类场景、多轮对话、附件与图片识别、评测、看板、合规
.\.venv\Scripts\python.exe scripts\smoke_test.py

# 课程空间（学习通式教学闭环）自检：任务点、测验评分、AI 助教回帖、签到、通知、班级统计、AI 导读/出题/学情建议
.\.venv\Scripts\python.exe scripts\seed_lms.py        # 首次：灌演示课程数据（--reset 可重灌）
.\.venv\Scripts\python.exe scripts\smoke_lms.py       # 全部检查（含 AI，约 3~4 分钟）
.\.venv\Scripts\python.exe scripts\smoke_lms.py --fast  # 只验数据链路，跳过 AI 生成
```

> 第 8 节含两项图片能力检查：本机若未启动视觉模型，脚本会明确标注「跳过」而不会计作通过；
> `smoke_lms.py --fast` 同理，跳过的 AI 检查不计入通过。

---

## 2. 赛题功能要求的落地情况

赛题要求"至少满足第 1—4 项，鼓励突破第 5—8 项"。本平台 **8 项全部实现**，逐条对照见
[需求对照表](docs/需求对照表.md)。

| # | 赛题要求 | 本平台的实现 |
| --- | --- | --- |
| 1 | 深度贴合场景需求 | 助学/助教/助研 3 场景 × 12 个智能体，工作流步骤按真实教学流程编排；内容遵循 GB/T 7714 著录规范与代码规范 |
| 2 | 交互自然流畅 | 会话持久化 + 指代消解（"那它呢"自动继承上文主题）+ 多轮追问 + 后续问题推荐 + 附件参与对话 |
| 3 | 知识权威可信 | 11 份结构化文档 / 81 个知识块，带 `source_type`、`authority(1-5)`、作者、年份、出版社元数据；检索按权威度加权 |
| 4 | 内容可追溯 | 每个知识块有 `locator`（文档 · 章节 · 页码），回答逐句标注 `[n]`，引用卡片可展开原文，工作流轨迹全程留痕 |
| 5 | 可复制可推广 | 学科/课程/知识库/智能体全部配置化（改环境变量即可换学科）；55 人真实数据验证；一键部署脚本 |
| 6 | 内容规范性 + 可自定义 | 规范性：GB/T 7714 著录、复杂度标注、代码规范检查；自定义：学习者画像、生成风格、课时/学段/难度参数 |
| 7 | 复杂问题求解 | `复杂问题求解` 智能体：问题界定 → 任务拆解 → 分层求解 → 踩坑预警 → 可执行第一步，配套自测题与思维拓展 |
| 8 | 个性化与自适应 | 学习画像（水平/目标/风格/深度/薄弱点）驱动讲解深度与练习推荐；薄弱点从错题与提问中自动归纳 |

技术建议的落地：**知识库构建**（Markdown 分块 + 元数据）、**模型微调**（LoRA/SFT 脚本与指令数据集）、**RAG**（BM25 + 向量混合检索）、**智能工作流编排**（声明式注册 + 分支 + 轨迹）、**多模态**（PDF/Word/Excel 解析 + 本地视觉模型 Qwen3-VL 识图 + 表格图表生成）、**场景化开发**（三场景闭环）。

---

## 3. 技术架构

```text
┌──────────────────────────────────────────────────────────────┐
│ 前端 SPA（原生 JS + Tailwind，Flask 同源托管）                 │
│  智能问答 · 学习/教学/科研工作台 · 课程空间 · 学习画像 · 知识库 · 看板 │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST /api/v1（Bearer Token + Session）
┌───────────────────────────▼──────────────────────────────────┐
│ Flask 应用层                                                  │
│  routes.py（鉴权/资源/聊天）  api_ext.py（智能体/知识库/评测）   │
├──────────────────────────────────────────────────────────────┤
│ 智能体工作流引擎  agents.py                                    │
│  声明式注册 → 检索步骤 → 工具步骤 → 条件分支 → 引用核验 → 轨迹   │
├──────────────────────────────────────────────────────────────┤
│ 检索与生成                                                    │
│  kb_service.py：章节分块 / 元数据 / BM25+向量混合检索 / 权威加权 │
│    └─ 预计算索引（加载时分词一次）+ 可选 C++ 内核 kbcore        │
│       逐块打分：81 块 0.1 ms，4.86 万块 5.2 ms（见 §4.1）      │
│  llm_client.py：本机 Ollama qwen3-vl:8b（默认，能识图）/ 星火    │
│                  / OpenAI 兼容 / 离线检索编排（无模型时诚实降级） │
│  vision_service.py：本机 Ollama（Qwen3-VL）/ OpenAI 兼容视觉服务 │
├──────────────────────────────────────────────────────────────┤
│ 数据层  models.py：会话/消息/画像/反馈/埋点/评测/知识库登记       │
│  lms_models.py：课程/章节/进度/测验/作答/讨论/签到/通知          │
│  SQLite（默认，data/smart_edu.db）或 MySQL                     │
└──────────────────────────────────────────────────────────────┘
```

**为什么可信**：检索层用 BM25（精确术语）+ 哈希向量（语义近似）+ 查询词覆盖率 + 连续短语命中 + 权威度加权共同打分，避免"只命中一个罕见词就被排到第一"；生成层强制模型只依据给定材料作答，并对每个 `[n]` 做存在性校验，未使用的引用会被显式剔除。

**为什么可复现**：无 GPU、无外网、无 MySQL 也能完整跑通。此时 `llm_client` 不再假装"大模型在思考"，而是切换为**离线检索编排**：按工作流把知识库原文结构化摘录出来，逐句带引用，并在界面顶部明示"离线检索模式"。

---

## 4. 目录结构

```text
SmartEduPlatform/
├── run.ps1                      # 一键启动：检查环境 → 拉起 Ollama → 启动后端（-Check 只自检）
├── requirements.txt             # 依赖清单（唯一来源；backend/requirements.txt 仅作兼容指向）
├── .env.example                 # 配置模板（复制为 .env；根目录优先，backend/.env 兼容）
├── backend/                     # 后端（21 个模块 + C++ 内核封装）
│   ├── app.py                   # 应用入口：蓝图注册、自动迁移、前端托管、预热
│   ├── config.py                 # 全部可配置项（学科/课程/模型/知识库/评测），读 .env
│   ├── models.py                 # 平台 12 张表：用户/作业/会话/消息/画像/反馈/埋点/评测
│   ├── lms_models.py             # 课程空间 12 张表：课程/成员/章节/进度/测验/作答/讨论/签到/通知
│   ├── lms_routes.py             # 课程空间接口 + AI 导读/出题/助教回帖/学情建议
│   ├── routes.py                 # 鉴权、资源、聊天、代码评测、学情诊断
│   ├── api_ext.py                # 智能体运行、知识库、画像、反馈、看板、评测
│   ├── agents.py                 # 工作流引擎 + 12 个学科智能体
│   ├── kb_service.py             # 知识库：分块、预计算索引、混合检索、引用格式化
│   ├── kb_native.py              # C++ 检索内核的 ctypes 封装（缺失时自动回退纯 Python）
│   ├── llm_client.py             # 生成后端（ollama/local/spark/openai/offline）+ 离线编排
│   ├── vision_service.py         # 视觉模型（识图 / 读文档里的图）
│   ├── text_utils.py             # 中文分词、BM25、哈希向量、检索用词清洗
│   ├── doc_parser.py             # PDF/Word/Excel/CSV/图片解析
│   ├── telemetry.py              # 使用埋点与看板聚合
│   ├── auth_utils.py             # 鉴权（Session + Bearer Token）
│   ├── native/kbcore.dll         # 编译产物（scripts/build_kbcore.ps1 生成，可不入库）
│   └── rag_service.py            # 兼容层（旧接口 → 新工作流）
├── native/                      # C++ 检索内核源码（kbcore.cpp，无第三方依赖）
├── frontend/                    # 前端（同源托管，无构建步骤）
│   ├── index.html
│   ├── js/main.js               # 登录、资源页、语音、主题
│   ├── js/agents.js             # 智能体工作台、对话、知识库、看板、画像
│   ├── js/lms.js                # 课程空间：任务点/测验/讨论/签到/通知/学情
│   └── css/style.css
├── data/                        # 见 data/README.md
│   ├── knowledge_base_docs/     # 学科知识库源文档（教材/标准/大纲/论文/案例）
│   ├── vector_db/               # 索引缓存（可重建）
│   ├── eval/testset.jsonl       # 12 题标准测试集；eval/reports/ 为每次报告
│   ├── attachments/ video/ materials/
│   └── smart_edu.db             # SQLite 演示库（MySQL 可用时不使用）
├── scripts/                     # 见 scripts/README.md（seed / smoke / check / 环境 / 性能）
├── training/                    # LoRA 微调（build_dataset.py / train_lora.py；权重需自行下载）
├── models/                      # 见 models/README.md —— 本地推理运行时与模型（约 6.6 GB，不进版本库）
│   ├── ollama/
│   │   ├── runtime/             # Ollama 0.40.12 本体 + lib\ollama（CUDA/Vulkan 后端，2.1 GB）
│   │   ├── store/               # OLLAMA_MODELS：qwen3-vl:8b 权重 + 清单 + 元数据（5.4 GB）
│   │   ├── ollama.cmd           # 带环境变量的便捷入口（list / ps / serve / pull）
│   │   └── README.md            # 启动方式与故障排查
│   └── llm/                     # （空）微调基座权重放这里，默认不下载
├── docs/                        # 参赛文档 01–06 + 需求对照表 + 图片 + 赛题原件
│   ├── 赛题/                    # 赛题 PDF 原件
│   └── handoff/                 # 与 AI 协作的交接记录（含完整对话）
├── native/                      # C++ 检索内核源码（kbcore.cpp）
└── .venv/                       # 本地虚拟环境（不进版本库）
```

> 判断"当前用的是哪个模型"不要看文档，看 `GET /api/v1/health/stack` 的 `llm` 字段
> （`provider / model / vision`）——界面右上徽标、设置面板都实时读这个接口。
>
> 判断"检索跑在哪个引擎上"同理，看 `knowledge_base.engine`（`cpp` / `python`）与
> `engine_version`、`engine_note`——C++ 内核缺失时会**自动回退**并在 `engine_note` 写明原因。

---

## 4.1 检索性能：为什么上 C++，以及实测数字

先说结论：**推理延迟由 8B 模型决定（23~40 秒），换语言改不了它**；能压的是检索这一段。
改动前的实现每次 `search()` 会把每个知识块重新分词 4~5 次，属于纯算法浪费。

| 知识块数 | 原实现 p50 | Python 预计算索引 | C++ 内核 | C++ 相对原实现 |
| --- | --- | --- | --- | --- |
| 81（当前演示库） | 24.1 ms | 0.3 ms | **0.1 ms** | 241× |
| 810 | 227.8 ms | 2.2 ms | **0.2 ms** | 1139× |
| 4,860 | 1,372.7 ms | 16.6 ms | **0.7 ms** | 1961× |
| 16,200 | 4,722.8 ms | 51.7 ms | **1.9 ms** | 2486× |
| 48,600（学科级规模） | 14,032.6 ms | 152.7 ms | **6.1 ms** | 2300× |

复现命令（本机 RTX 4060 / i7，5 题 × 3 轮取 p50）：

```powershell
python scripts\bench_kb.py --engine python     # Python 引擎
python scripts\bench_kb.py --engine cpp        # C++ 内核
python scripts\verify_kb_equiv.py              # 两引擎与原实现的 top-10 逐位比对（必须一致）
```

**分工**：C++（`native/kbcore.cpp`）只做 `O(N×Q)` 的逐块打分——BM25、标题命中、正文覆盖率、
成对概念惩罚、学科实体奖惩；分词、向量余弦（numpy/BLAS）、引用格式化留在 Python。
C++ 只接收已分好的 token id 数组，所以两边分词结果完全一致，可以直接做逐位等价校验
（30 条查询 top-10 的 `chunk_id / score / coverage` 全部相同）。

**可选依赖**：内核是纯 C++17、零第三方库，`scripts\build_kbcore.ps1` 一条命令编译
（MinGW g++ 或 clang++，全静态链接，不依赖 MinGW 运行时 DLL）。没编译也能跑：
`kb_service` 检测不到动态库就自动回退纯 Python 引擎，功能一致、只是慢一些；
`KB_ENGINE=python` 可强制回退，`KB_ENGINE=cpp` 可强制要求内核。

> 用 ctypes + C ABI 而不是 pybind11：本机 Python 由 MSVC 编译，MinGW 产出的 `.pyd`
> 加载不了；C ABI 动态库与编译器无关，也不需要装 Visual Studio。

---

## 5. 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health`、`/meta`、`/health/stack` | 健康检查、能力矩阵、模型与知识库状态 |
| GET | `/api/v1/agents` | 三场景 12 个智能体及其输入模式 |
| POST | `/api/v1/agents/<id>/run` | 运行指定智能体工作流，返回答案+引用+轨迹+图表 |
| POST | `/api/v1/chat` | 多轮对话（会话续接、附件、引用、AI 标识） |
| POST | `/api/v1/chat/stream` | SSE 流式对话 |
| GET/POST | `/api/v1/conversations`、`/conversations/<id>` | 会话列表与历史消息 |
| GET/PATCH | `/api/v1/profile` | 学习画像读写 |
| POST | `/api/v1/feedback` | 用户反馈（评分/有用度/评论） |
| GET | `/api/v1/analytics/overview` | 使用规模、质量、趋势、智能体分布 |
| GET/POST/DELETE | `/api/v1/kb/stats`、`/kb/docs`、`/kb/search`、`/kb/upload`、`/kb/rebuild` | 知识库全生命周期 |
| POST | `/api/v1/eval/run` | 运行标准测试集评测并生成 Markdown 报告 |
| GET | `/api/v1/compliance/statement` | 伦理与安全合规声明的机器可读版本 |
| GET | `/api/v1/lms/courses`、`/lms/courses/<id>`、`/lms/overview` | 课程列表、课程详情（含章节与班级统计）、我的待办 |
| POST | `/api/v1/lms/courses`、`/lms/courses/join` | 教师建课、学生凭邀请码加入 |
| POST | `/api/v1/lms/chapters/<id>/intro`、`/progress` | AI 课前导读（带引用、带缓存）、任务点进度 |
| GET/POST | `/api/v1/lms/courses/<id>/quizzes`、`/lms/chapters/<id>/quiz`、`/lms/quizzes/<id>/submit` | 测验列表、AI 按章节出题、提交判分与逐题解析 |
| GET/POST | `/api/v1/lms/courses/<id>/discussions`、`/lms/posts/<id>/reply` | 讨论区；发帖后 AI 助教后台生成带引用的回帖 |
| GET/POST | `/api/v1/lms/courses/<id>/signins`、`/signin`、`/lms/signins/<id>/checkin` | 签到发起 / 签到码校验 |
| GET/POST | `/api/v1/lms/courses/<id>/notices`、`/lms/notices/<id>/read` | 通知发布、已读状态 |
| GET/POST | `/api/v1/lms/courses/<id>/dashboard`、`/advice` | 学情看板（个人/班级两套口径）、AI 学情建议 |

---

## 6. 接入真实大模型

**本机默认**：`qwen3-vl:8b` 与 Ollama 运行时都随项目附带（`models/ollama/`），`.\run.ps1` 会自动拉起并检查；
平台用它作答，并且它能**直接看图**（全本地，推理不出网）：

```powershell
# backend/.env 里已写好，等价于：
$env:LLM_PROVIDER = "ollama"          # 本机视觉大模型（能对话，也能直接读图）
$env:VISION_MODEL = "qwen3-vl:8b"
# 地址与模型名自动推导为 http://127.0.0.1:11434/v1 + qwen3-vl:8b
# 模型库由 models/ollama/store 提供（run.ps1 设好 OLLAMA_MODELS）
```

实测（RTX 4060 Laptop 8GB，8B 模型满负载）：纯文本问答 ≈38–40 秒/题，读图 ≈27 秒/张（看图问答 ≈43 秒），读 .docx/.xlsx/.md 附件同样直接吃正文。显存占用约 7.5/8.1 GiB；为保证稳定，Ollama 以 `KEEP_ALIVE=30m / MAX_LOADED_MODELS=1 / NUM_PARALLEL=1` 运行（见 `scripts/start_ollama.ps1`），模型常驻、不切模型、不并行，8 GB 显存机器不会 OOM。

```powershell
# 其它可选后端（都不是必须的）
$env:LLM_PROVIDER = "local"           # 直连 HuggingFace 权重（纯文本，需自行下载，见 models/README.md）
$env:LLM_PROVIDER = "spark"           # 讯飞星火；配合 SPARK_API_KEY
$env:LLM_PROVIDER = "openai"          # 任意 OpenAI 兼容网关
$env:LLM_BASE_URL = "https://your-gateway/v1"
$env:LLM_API_KEY  = "sk-xxx"
$env:LLM_MODEL_NAME = "your-model"
```

未配置或后端不可用时，平台自动回落到离线检索模式，界面会明确提示当前状态，不会伪装成大模型输出。
`auto` 的选择顺序：本机 Ollama（视觉模型）→ 本地权重 → 星火 → OpenAI 兼容 → 离线检索。

> **关于 "`local` 后端"**：仓库内已不再内置任何权重（原来的 `Qwen2.5-7B-Instruct` 是纯文本模型、不能识图，
> 只有微调脚本会用到，已删除，见 `models/README.md`）。所以现在**能识图的模型只有一个入口**：本机 Ollama 的 `qwen3-vl:8b`。

**微调**（基座需自行下载，`--base` 为必填）：

```powershell
.\.venv\Scripts\python.exe training\build_dataset.py                     # 构建学科指令数据集
huggingface-cli download Qwen/Qwen3-VL-8B-Instruct --local-dir models/llm/Qwen3-VL-8B-Instruct
.\.venv\Scripts\python.exe training\train_lora.py --base models/llm/Qwen3-VL-8B-Instruct --epochs 3
.\.venv\Scripts\python.exe training\train_lora.py --merge --base models/llm/Qwen3-VL-8B-Instruct --adapter training\output\... 
```

---

## 7. 换一个学科要改什么

平台所有学科相关内容都在配置与知识库中，**代码零改动**：

```powershell
$env:DISCIPLINE_NAME = "临床医学"
$env:DISCIPLINE_COURSE = "内科学"
$env:KB_DOC_DIR = "data/kb_medicine"     # 放入该学科的结构化文档
```

再执行一次索引重建（知识库页面点"重建索引"或 `POST /api/v1/kb/rebuild`）即可。智能体工作流的步骤是通用的（检索→结构化→核验），换学科只需换知识库与提示词模板。

---

## 8. 文档索引

| 文档 | 对应赛题交付物 |
| --- | --- |
| [需求对照表](docs/需求对照表.md) | 功能点逐条自评 |
| [伦理与安全合规性声明](docs/02-伦理与安全合规性声明.md) | 《02—伦理与安全合规性声明》 |
| [演示与部署说明](docs/03-演示与部署说明.md) | 《03—作品Demo》文档部分 |
| [作品方案](docs/04-作品方案.md) + `docs/灵眸智教-作品方案.pptx` | 《04—作品方案》 |
| [效果验证报告](docs/06-效果验证报告.md) | 《06—效果验证报告》 |
| `scripts/smoke_test.py`、`data/eval/reports/` | 《07—其他材料》测试脚本与测试报告 |
| `docs/赛题/` | 赛题原件（XH-202620 PDF） |
| `docs/handoff/` | 与 AI 协作的交接记录（含完整对话，便于追溯改动） |

---

## 9. 已知边界

- 仓库（Git）里只有源码：模型与推理运行时体积 6.6 GB，放在 `models/ollama/`（已在 `.gitignore` 中忽略），
  随项目文件夹**整体拷贝**分发；不需要另装 Ollama，也不需要联网拉模型。
  未启动模型时回答为"知识库原文结构化摘录"，可核查但不如大模型连贯，界面已明确标识。
- 哈希向量后端只做近似语义匹配，检索主力是 BM25 + 覆盖率 + 短语命中；安装 `sentence-transformers` 后可自动升级为语义向量后端。
- 55 名学生的数据由 `scripts/seed_demo.py` 生成，用于演示与规模验证口径；真实试用反馈见《06—效果验证报告》说明。
- 「课程空间」的章节/作答/签到记录由 `scripts/seed_lms.py` 生成（同一学号每次生成结果一致），属**演示数据**；真实使用需导入名单后自然积累。
- 课程空间的 AI 生成是**同步调用**（导读/出题/学情建议 25–50 秒）或**后台线程 + 轮询**（讨论区助教回帖），单机 8B 模型下并发能力有限；生产化需接推理队列。
- 视频/课件上传依赖本机磁盘，未接入对象存储；并发与权限按课程级单实例设计。