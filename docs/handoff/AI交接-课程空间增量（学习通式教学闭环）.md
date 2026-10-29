# 后续增量说明：课程空间（学习通式教学闭环）

> 本文件是对《AI交接-灵眸智教项目与完整对话.md》的**增量补充**。原交接文件描述的是到"本机视觉模型 qwen3-vl:8b 替换完成"为止的状态；
> 之后又按用户要求"仿照学习通加功能，但仍围绕本地大模型与赛题 PDF"新增了一整个课程空间模块。以下是可直接接手的信息。

## 1. 用户原话与意图

> "把学习通的一些功能加进来，仿照学习通，但是还是得围绕本地大模型这个主题和我发给你的那个pdf文档"

即：**形态参考超星学习通**（课程 / 章节任务点 / 随堂测验 / 讨论 / 签到 / 通知 / 学情），
但每个 AI 能力必须跑在**本机模型**上，并能回答赛题 PDF 里的"贴合场景、规模化、个性化、可追溯"。

## 2. 新增文件

| 文件 | 作用 |
| --- | --- |
| `backend/lms_models.py` | 12 张 `lms_*` 表：课程 / 成员 / 章节 / 进度 / 测验 / 作答 / 帖子 / 回复 / 签到 / 签到记录 / 通知 / 已读 |
| `backend/lms_routes.py` | 蓝图 `lms_bp`，前缀 `/api/v1/lms`：全部读写接口 + 4 处 AI 生成 |
| `frontend/js/lms.js` | 课程空间单页逻辑（任务点 / 测验 / 讨论 / 签到 / 通知 / 学情），约 1000 行 |
| `scripts/seed_lms.py` | 演示数据：1 门课、8 章 7 个任务点、55 名学生、6 次签到、3 条通知、1 帖 1 回 |
| `scripts/smoke_lms.py` | 27 项接口自检（含 4 处真实生成），`--fast` 跳过 AI |
| `scripts/check_handlers.py` | 静态检查：`onclick` 里调用的函数是否都定义了 |
| `docs/img/lms-*.png` | 4 张真实运行截图（任务点 / 测验 / 讨论帖 / 教师学情） |

改动文件：`backend/app.py`（注册蓝图 + 能力矩阵加 `lms` 项）、`backend/config.py`（`DATABASE_URL` 支持相对路径）、
`backend/.env`（新增 `DATABASE_URL=sqlite:///data/smart_edu.db`，把数据源钉死在本地 SQLite）、
`frontend/index.html`（侧边栏「课程空间」+ `page-course` + 引入 `js/lms.js`）、
`frontend/js/agents.js`（`switchPage` 增加 course 分支——注意：**真正生效的是 agents.js 里的那个 switchPage**，
main.js 里还有一个同名函数但被覆盖）、README / docs 03、04、06 / 需求对照表 / `docs/build_pptx.py`（新增第 5 页）。

## 3. 接口清单（全部需要登录，Bearer 或 Session）

```text
GET    /api/v1/lms/overview                      我的待办 + 未读通知（跨课程聚合）
GET    /api/v1/lms/courses                       课程列表（含任务点/测验/签到/未读统计）
POST   /api/v1/lms/courses                       教师建课（自动生成邀请码）
POST   /api/v1/lms/courses/join                  学生凭邀请码加入
GET    /api/v1/lms/courses/<id>                  课程详情（章节 + 我的进度；教师额外拿到班级统计）
POST   /api/v1/lms/chapters/<id>/intro           AI 课前导读（缓存，refresh=true 重新生成）
POST   /api/v1/lms/chapters/<id>/progress        任务点进度 todo/doing/done
GET    /api/v1/lms/courses/<id>/quizzes          测验列表（教师可见答案）
POST   /api/v1/lms/chapters/<id>/quiz            AI 按章节出题（3–8 题）
GET    /api/v1/lms/quizzes/<id>                  试卷详情
POST   /api/v1/lms/quizzes/<id>/submit           提交判分 + 逐题解析
GET/POST /api/v1/lms/courses/<id>/discussions    讨论区；发帖后 AI 助教后台线程回帖
GET    /api/v1/lms/posts/<id>                    帖子详情（含 AI 回帖与引用）
POST   /api/v1/lms/posts/<id>/reply              人工回帖
GET/POST /api/v1/lms/courses/<id>/signins|signin 签到列表 / 教师发起签到
POST   /api/v1/lms/signins/<id>/checkin|close    签到码校验 / 结束签到
GET/POST /api/v1/lms/courses/<id>/notices        通知列表 / 发布
POST   /api/v1/lms/notices/<id>/read             标记已读
GET    /api/v1/lms/courses/<id>/dashboard        学情（个人口径；教师拿到班级口径 + 预警名单）
POST   /api/v1/lms/courses/<id>/advice           AI 学情建议（只喂真实统计）
```

## 4. 四个 AI 生成点（本地 qwen3-vl:8b 实测）

| 能力 | 实现 | 实测 | 留痕 |
| --- | --- | --- | --- |
| 课前导读 | `_ai_intro()` 检索 4 个片段 → 固定四段结构 | 564–703 字 / ≈24.6 s / 3 条引用 | `intro_cache` + `intro_meta`（provider/model/latency/citations） |
| 随堂出题 | `_ai_quiz()` 强约束输出 JSON 数组，校验 4 选项 + 答案下标 | 5 题 / ≈29 s | `quizzes.ai_meta` |
| 助教回帖 | `_ai_tutor_reply()` 在 `threading.Thread` 里跑，前端 6 s 轮询 | ≈199 字 / 30–45 s / 3 条引用 | `lms_replies.is_ai/ai_model/citations` |
| 学情建议 | `learning_advice()` 只传真实统计，要求"不要虚构数字" | 169 字 / ≈5 s | 响应里的 `meta` |

判定阈值写死在 `lms_routes.class_stats`：任务点 <60%、测验 <60%、出勤 <80% 任一命中即进预警名单。

## 5. 踩过的坑（接手时别再踩）

1. **`get_llm().complete()` 返回的是 `LLMResult` 对象，不是 dict** —— 必须 `getattr(result, 'text')`；
   `lms_routes._chat()` 已统一转成 dict，新代码复用它。
2. **`switchPage` 有两个定义**：main.js:805 和 agents.js:92，后者覆盖前者。加页面分支要改 agents.js。
3. **数据源会分叉**：MySQL 服务一旦起来，配置会自动改用 `qwen_db`（里面是 3 条旧数据），
   而演示数据在 `data/smart_edu.db`。已在 `.env` 里显式 `DATABASE_URL=sqlite:///data/smart_edu.db` 钉死；
   `config._database_uri()` 支持这种相对写法（相对项目根目录）。
4. **`login` 会刷新 `api_token`**：跑完 `smoke_test.py` 之后，之前抓的 token 可能失效，
   截图/脚本要用新 token（表现为页面只剩登录遮罩、截图为深色）。
5. 表名不是 `lms_discussion_posts`，而是 `lms_posts` / `lms_replies` / `lms_chapters` / `lms_chapter_progress`。
6. `.ps1` 脚本必须以 UTF-8 BOM 保存；`.py` 里文档字符串含 `\.` 要么加 `r` 前缀要么转义。

## 6. 现在怎么验

```powershell
# 后端（Ollama 已在跑：qwen3-vl:8b）
cd backend; ..\.venv\Scripts\python.exe app.py

# 课程空间：灌数据 → 自检
cd ..; .\.venv\Scripts\python.exe scripts\seed_lms.py --reset
.\.venv\Scripts\python.exe scripts\smoke_lms.py            # 27 项，含 4 处 AI（3~4 分钟）
.\.venv\Scripts\python.exe scripts\smoke_test.py          # 主套 36 项，仍全通过
```

浏览器打开 `http://127.0.0.1:5000/`，账号 `student2023010101@smartedu.cn / Student@123` 或
`teacher@smartedu.cn / Teacher@123`，左侧「课程空间」；邀请码 `DS2026`。
深链：`/#course-chapters`、`/#course-quiz`、`/#course-data`、`/#course-discuss-2`。

## 7. 下一步可以做什么（按价值排序）

1. **作业模块与课程空间打通**：现在 `homeworks` 是全局的，作业还没挂到课程/章节下，教师批改也没有回写任务点完成度。
2. **测验支持多题型**：目前只有单选；填空/编程题可复用 `routes.py` 的代码评测能力。
3. **AI 生成进推理队列**：8B 单机并发有限，中途刷新页面会丢生成任务；改成任务表 + 状态轮询更稳。
4. **课程导入**：从 Excel/教务 CSV 导入名单与章节，替代 `seed_lms.py`。
5. **学习行为埋点接入看板**：`ChapterProgress` 的学习时长可以并入 `usage_events`，让"使用与评测看板"覆盖教学闭环。

## 8. 诚实边界（对外必须这么说）

- 55 名学生、40 条作答、6 次签到记录都是 `scripts/seed_lms.py` **生成**的演示数据，同一学号每次结果一致、可复现，但**不代表真实教学行为**。
- AI 生成是同步阻塞（导读/出题/建议 25–50 s）或后台线程 + 轮询（助教回帖），单机 8B 并发能力有限。
- 签到、通知、判分**刻意不用模型**，这是设计选择，不是能力缺失。
---

## 9. 后续变更（本次）：只留可识图模型 + 项目结构标准化

### 9.1 删掉的模型

`models/llm/qwen--Qwen2.5-7B-Instruct/snapshots/master`（**14.2 GB，Qwen2.5-7B-Instruct，纯文本，不能识图**）已删除。
它此前只作为 `training/train_lora.py` 的默认基座，平台运行时从未加载（`.env` 里没有 `LLM_MODEL_PATH`，`LLM_PROVIDER=ollama`）。
连带处理：

- `training/train_lora.py`：`--base` 改为**必填**，docstring 改为推荐 `Qwen/Qwen3-VL-8B-Instruct`（与线上同源）；
- `backend/llm_client.py`：`_LOCAL_MODEL_CHOICES` 增加 `Qwen3-VL-8B-Instruct` 作为首选候选目录名；
- `models/README.md`：说明本目录默认空、权重按需下载、为何删掉 14.2 GB。

**当前能识图的模型只有一个入口**：本机 Ollama 的 `qwen3-vl:8b`（另有 `qwen3-vl:4b` 作为低显存备选，同为视觉模型）。

### 9.2 目录调整（改动位置 → 新位置）

| 原位置 | 新位置 | 原因 |
| --- | --- | --- |
| `backend/rag_qa.py`、`build_knowledge_base.py`、`create_tables.py`、`test_knowledge_base.py`、`test_system.py` | `legacy/` | 均无任何代码引用，且依赖 langchain/FAISS/torch（当前依赖清单里没有），保留只为追溯 |
| `AI交接-*.md`（2 个，根目录） | `docs/handoff/` | 根目录只留工程文件 |
| `XH-202620_....pdf`（根目录） | `docs/赛题/` | 赛题原件归档 |
| `backend/requirements.txt`（内容） | 根 `requirements.txt`；`backend/requirements.txt` 改为 `-r ../requirements.txt` | 依赖清单唯一来源 |
| `backend/.env.example`（内容） | 根 `.env.example`；`backend/.env.example` 改为指针说明 | 配置模板唯一来源 |

新增：`run.ps1` / `run.sh`（一键启动，`-Check` 只自检）、`scripts/README.md`、`data/README.md`、`models/README.md`、`legacy/README.md`、
重写 `.gitignore`。`backend/config.py` 现在**先读根目录 `.env`、再读 `backend/.env`**（后者仅兼容，不覆盖）。

### 9.3 验证

| 检查 | 结果 |
| --- | --- |
| `scripts/smoke_test.py` | 36/36 通过（含图片识别与引用检查） |
| `scripts/smoke_lms.py --fast` | 21 通过 / 0 失败 / 4 跳过 |
| `GET /api/v1/health/stack` | `llm: ollama / qwen3-vl:8b / vision=true`，`degraded=false` |
| `.\run.ps1 -Check` | 环境/数据库/Ollama/知识库索引全部 OK |
| `scripts/check_dom_ids.py` + `check_handlers.py` | 无缺失、无未定义函数 |
| `compileall backend scripts training legacy` | 通过 |
| 项目体积（不含 `.venv`） | 由约 14.5 GB 降到 **11 MB** |

> 注意：`run.ps1` 必须保存为 **UTF-8 with BOM**，否则 Windows PowerShell 5.1 会把中文读成乱码并报语法错误（已踩过）。

---

## 10. 再进一步：只保留一个 8B 视觉模型，并把它跑稳

### 10.1 删掉的东西

| 对象 | 大小 | 说明 |
| --- | --- | --- |
| `qwen3-vl:4b`（Ollama 模型） | 3.0 GB | 低显存备选，现已不需要 |
| `gguf/Qwen3VL-4B-Instruct-Q4_K_M.gguf` | 2.33 GB | 4B 的导入源文件 |
| `gguf/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` | 0.42 GB | 4B 的视觉投影层 |
| `gguf/Modelfile`（内容为 4B） | — | 已被 `Modelfile.8b` 取代 |

现在 `ollama list` 只有 `qwen3-vl:8b`；`models/` 目录里保留 `Qwen3VL-8B-Instruct-Q4_K_M.gguf` + `mmproj-…-8B` + `Modelfile.8b`
（这一对是 8B 模型的**导入源**，与 `blobs/` 是重复的 5.38 GB；确认不再需要离线重建时可删，`ollama pull qwen3-vl:8b` 可恢复）。

### 10.2 跑稳的四个参数（写进 `scripts/start_ollama.ps1`）

```powershell
$env:OLLAMA_KEEP_ALIVE = '30m'          # 常驻，避免每次 ~20 秒冷启动
$env:OLLAMA_MAX_LOADED_MODELS = '1'     # 只驻留一个模型，杜绝切模型时 OOM
$env:OLLAMA_NUM_PARALLEL = '1'          # 单并发，避免并行请求撑爆 8 GB 显存
$env:OLLAMA_FLASH_ATTENTION = '1'       # 省显存、略快
```

`backend/vision_service.py` 的 `/api/generate` 请求也带上 `keep_alive`（来自 `VISION_KEEP_ALIVE`，
默认 `30m`），文本侧走 OpenAI 兼容端点，由服务端同名环境变量兜底。

> 注意：`OLLAMA_MODELS` 指向 `models\ollama\store` 是**必须**的（运行时已随项目附带，见 §12）——
> 不设这个变量，Ollama 会去 `%USERPROFILE%\.ollama\models` 找模型，结果"模型没了"。

### 10.3 实测（模型 `num_ctx=8192`）

| 场景 | 结果 |
| --- | --- |
| 冷启动第 1 次识图 | 28.6 s（含加载权重），回答正确 |
| 第 2 / 3 次识图 | 22.8 s / 31.7 s，未重新加载 |
| `/api/ps` | `qwen3-vl:8b`、`size_vram≈5.44 GB`、`context_length=8192`、`expires_at≈+30 min` |
| 显存 | 7513–7526 / 8188 MiB |
| `scripts/smoke_test.py` | **36/36 通过** |
| `scripts/smoke_lms.py --fast` | **21 通过 / 0 失败 / 4 跳过** |

### 10.4 文档同步

`README.md`（环境要求、可选模型段）、`docs/03`（§九 增加"只保留一个模型 + 四个稳定参数"表）、
`docs/06`（生成模式写错成 `qwen3-vl:4b`，已改正为 `qwen3-vl:8b`）、`scripts/README.md`、
`backend/config.py`（注释与新增 `VISION_KEEP_ALIVE`）、`.env.example`（新增 keep_alive 与 Ollama 参数说明）。

---

## 11. 性能改造：C++ 检索内核（kbcore）

### 11.1 先测再改：延迟到底花在哪

| 环节 | 81 块 | 4.86 万块 |
| --- | --- | --- |
| HTTP + JSON（Flask） | p50 2 ms | 2 ms |
| `/kb/search` 检索 | p50 17 ms | **9.2 s** |
| 离线问答全链路（无模型） | 40–53 ms | — |
| 本机 8B 生成一次 | **23 000–40 000 ms** | — |

结论：Python 全栈只占端到端 0.05%–0.2%，**换语言不可能降低用户感知延迟**；
真正的墙是「每次 search 都把每个块正文重新分词 4~5 次」（85% 耗时）。

### 11.2 两步改动

1. **预计算索引**（`backend/kb_service.py` 的 `_ChunkIndex`）：加载/重建时分词一次，
   同时算好 `token_set`、`token_str`、标题集合、成对概念位掩码、实体位掩码、权威度数组。
   81 块：24.1 ms → 0.3 ms。
2. **C++ 内核**（`native/kbcore.cpp` + `backend/kb_native.py`）：逐块打分进 C++，
   通过 **C ABI 动态库 + ctypes** 调用（不用 pybind11：本机 Python 是 MSVC 编译的，
   MinGW 产出的 `.pyd` 加了不；C ABI 与编译器无关）。

### 11.3 实测（5 题 × 3 轮取 p50）

| 知识块数 | 原实现 | Python 预计算 | C++ 内核 | C++ vs 原实现 |
| --- | --- | --- | --- | --- |
| 81 | 24.1 ms | 0.3 ms | **0.1 ms** | 241× |
| 810 | 227.8 ms | 2.2 ms | **0.2 ms** | 1139× |
| 4,860 | 1,372.7 ms | 16.6 ms | **0.6 ms** | 1961× |
| 16,200 | 4,722.8 ms | 51.7 ms | **1.6 ms** | 2486× |
| 48,600 | 14,032.6 ms | 152.7 ms | **6.1 ms** | 2300× |

### 11.4 正确性怎么保证

- `scripts/verify_kb_equiv.py`：把改动前的打分逻辑原样留作 reference，
  逐题比对 top-10 的 `chunk_id / score / coverage`，两个引擎都必须**逐位相同**。
  当前 30 条查询（含成对概念、实体消歧、短语命中）全部一致。
- 一致性的前提是「分词只在 Python 做」：C++ 只接收 token id，不自己分词。
- 自动回退：DLL 缺失/加载失败 → `engine='python'`，`engine_note` 写明原因，检索照常工作。
- 已回归：`smoke_test.py` 36/36、`smoke_lms.py --fast` 21 通过，`/health/stack` 显示
  `knowledge_base.engine = cpp`、`engine_version = kbcore 1.0 (BM25 + 学科消歧, C++17)`。

### 11.5 踩过的坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Could not find module kbcore.dll` | MinGW 默认动态链接 `libwinpthread-1.dll` | 加 `-static -static-libgcc -static-libstdc++` |
| `ctypes.ArgumentError: int too long to convert` | 句柄是 Python int，函数声明成 `c_void_p` | 句柄包成 `ctypes.c_void_p`，并给 `kbc_search` 写全 `argtypes` |
| 引擎显示 cpp 但速度没变 | `bench_kb.py` 在 `_ensure_index()` 之后才设 `KB_ENGINE` | 环境变量必须在建索引前设置 |
| 编译成功但控制台中文乱码 | 子进程 stdout 走控制台代码页 | 构建脚本的自检输出改成 ASCII |

### 11.6 新增/改动文件

`native/kbcore.cpp`、`backend/kb_native.py`、`backend/native/kbcore.dll`（产物，已 gitignore）、
`scripts/build_kbcore.ps1`、`scripts/bench_kb.py`、`scripts/verify_kb_equiv.py`、
`backend/kb_service.py`（`_ChunkIndex` / `_score_python` / `_score_cpp` / `stats` 增加引擎字段）、
`backend/config.py`（`KB_ENGINE`）、`.env.example`、`README.md` §4.1、`docs/04` §3.3、`docs/06` §2.6。

> 回退命令：`$env:KB_ENGINE='python'` 就完全走纯 Python 引擎，两条路径都已验证等价。

---

## 12. 目录标准化：模型与运行时收进项目（E:\Tools 已清空）

### 12.1 为什么项目只剩 11 MB

代码一直是 11 MB；之前看起来"很大"是因为**权重放在项目外面**（`E:\Tools\ollama`，12.9 GB）。
已按要求整体搬进项目，现在拷走整个文件夹即可在无网机器上跑，不需要另装 Ollama、也不需要 `ollama pull`。

### 12.2 新布局

```text
models/
├── README.md                # 模型清单、启动方式、腾空间的办法
├── ollama/
│   ├── runtime/             # 2.1 GB：ollama.exe + lib\ollama（llama.cpp / CUDA v12,v13 / Vulkan）
│   ├── store/               # 10.8 GB：OLLAMA_MODELS（blobs / manifests-v2 / metadata / gguf）
│   ├── ollama.cmd           # 带环境变量的入口（list / ps / serve / pull）
│   └── README.md            # 本层故障排查
├── llm/                     # （空）微调基座权重
└── .gitkeep
```

`E:\Tools\ollama` 已删除；`%USERPROFILE%\.ollama` 不再被使用。

### 12.3 代码改动

| 文件 | 改动 |
| --- | --- |
| `run.ps1` | Ollama 路径改指向 `models\ollama\runtime`；自动设 `OLLAMA_MODELS=models\ollama\store`；`-Check` 多打印模型库与检索内核 |
| `run.sh` | 同上（Linux/macOS 需放对应平台的二进制） |
| `scripts/start_ollama.ps1` | 去掉硬编码 `E:\Tools\ollama`；补 `lib` 存在性与模型库空目录检查 |
| `models\ollama\ollama.cmd` | 新增：CRLF + ASCII 注释（cmd.exe 不吃 UTF-8 中文注释） |
| `.gitignore` | 忽略 `models/ollama/`、`models/llm/*`（12.9 GB 不入库） |
| `README.md` / `docs/03` | 环境要求加磁盘 13 GB；启动步骤改为"模型随项目附带"；目录树重画 |

### 12.4 坑（都已修）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Error: could not locate ollama app` | 设了 `OLLAMA_HOME`，新版 Ollama 据此找运行时目录 | **不要设** `OLLAMA_HOME`，只设 `OLLAMA_MODELS` |
| `ollama.cmd` 报"'先设好环境变量' 不是内部或外部命令" | `.cmd` 被写成 LF + UTF-8 中文注释 | 改 CRLF + ASCII 注释 |
| 服务已起但 `list` 仍报 `could not locate ollama app` | 该报错其实是"没连上服务，且找不到 GUI 自启程序" | 先 `serve`，再 `list` |
| 换目录后提示"没有模型" | `OLLAMA_MODELS` 没生效 | 用 `run.ps1` 或 `models\ollama\ollama.cmd` 启动 |

### 12.5 验收

- `.\run.ps1 -Check`：`OK Ollama 已运行，模型: qwen3-vl:8b` + `OK 模型库: models\ollama\store（10.8 GB）` + `OK 检索内核: kbcore（576 KB）`
- `/api/v1/health/stack`：`provider=ollama`、`model=qwen3-vl:8b`、`vision=true`；`knowledge_base.engine=cpp`
- 端到端问答：实测 38.2 s，5 条引用，首条为「图论基础与最短路径 · 最短路径」

### 12.6 还能再省的空间

| 目录 | 体积 | 说明 |
| --- | --- | --- |
| `models/ollama/store/gguf/` | 5.38 GB | 当初 `ollama create` 的导入源（8B + mmproj），模型本体已在 `blobs/`，删掉仍能跑 |
| `models/ollama/runtime/lib/ollama/cuda_v13/` | 约 1 GB | 本机驱动 12.7 用不到 v13，只用 cuda_v12；换到新驱动机器再补 |
