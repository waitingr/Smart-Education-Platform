# scripts/ —— 运维、自检与演示数据脚本

所有脚本都在**项目根目录**执行，且用项目自带的虚拟环境（`.\.venv\Scripts\python.exe`）。

## 演示数据（可重复灌入）

| 脚本 | 作用 | 命令 |
| --- | --- | --- |
| `seed_demo.py` | 教师 + 55 名学生 + 4 次作业 + 196 份提交 + 使用记录 + 反馈 | `python scripts\seed_demo.py` |
| `seed_lms.py` | 课程空间：1 门课（邀请码 `DS2026`）/ 8 章 7 个任务点 / 40 条作答 / 6 次签到 / 3 条通知 | `python scripts\seed_lms.py`，`--reset` 先清空再灌 |

> 两者生成的都是**演示数据**（脚本结尾会明确打印提示），同一学号下结果可复现，不代表真实教学行为。

## 自检（可直接作为提交材料里的"测试脚本"）

| 脚本 | 覆盖范围 | 命令 |
| --- | --- | --- |
| `smoke_test.py` | 36 项端到端：健康检查、鉴权、知识库检索与引用、多轮对话、三场景、附件与**图片识别**、评测与看板、合规声明 | `python scripts\smoke_test.py`（约 5–10 分钟，含真实模型调用） |
| `smoke_lms.py` | 27 项课程空间：任务点、测验评分、AI 助教回帖、签到、通知、班级统计、AI 导读/出题/学情建议 | `python scripts\smoke_lms.py`；`--fast` 跳过 4 项 AI 生成 |

两项脚本都把「跳过」与「通过」分开计数：没有模型时图片检查标 `SKIP`，**不会**被算成通过。

## 静态检查（前端改动后跑一遍）

| 脚本 | 检查内容 | 命令 |
| --- | --- | --- |
| `check_dom_ids.py` | JS 里 `getElementById('x')` 引用的 id 是否真实存在于 HTML 或由 JS 动态生成 | `python scripts\check_dom_ids.py` |
| `check_handlers.py` | 模板里 `onclick="fn(...)"` 调用的函数是否都在 JS 中定义 | `python scripts\check_handlers.py` |
| `vision_probe.py` | 直接打本机 Ollama 视觉模型，验证"识图链路"本身是否可用（不经过 Flask） | `python scripts\vision_probe.py 图片.png` |

## 性能（检索内核）

| 脚本 | 作用 | 命令 |
| --- | --- | --- |
| `build_kbcore.ps1` | 编译 C++ 检索内核 `backend/native/kbcore.dll`（全静态链接，编完立刻自检能否被 ctypes 加载） | `powershell -ExecutionPolicy Bypass -File scripts\build_kbcore.ps1` |
| `bench_kb.py` | 检索压测：81 → 4.86 万块，输出索引构建耗时、p50/p95、QPS | `python scripts\bench_kb.py --engine cpp`（或 `--engine python`） |
| `verify_kb_equiv.py` | **等价性校验**：把改动前的打分逻辑作为基准，比对 top-10 的 `chunk_id / score / coverage` 是否逐位相同 | `python scripts\verify_kb_equiv.py`（`KB_ENGINE=cpp` 时校验内核） |

> 改过 `kb_service.py` 的打分逻辑、或改过 `native/kbcore.cpp`，**必须**跑一遍 `verify_kb_equiv.py`：
> 检索结果一变，引用编号就变，36 项自检和 12 题评测的结论都会跟着变。
> C++ 内核缺失/编译失败时系统自动回退纯 Python 引擎，`/api/v1/health/stack` 的
> `knowledge_base.engine` 会如实显示当前用的是哪个。

## 环境

| 脚本 | 作用 |
| --- | --- |
| `start_ollama.ps1` | 拉起**项目内**的 Ollama（`models\ollama\runtime`）、指向项目内模型库（`models\ollama\store`），并以稳定参数运行（`keep_alive=30m`、单模型、单并发）；`-Pull` 用于补拉模型 |

## 约定

- 脚本一律从项目根目录运行，内部用 `Path(__file__).resolve().parents[1]` 定位项目根；
- 需要 Flask 在跑的脚本（`smoke_*.py`）默认访问 `http://127.0.0.1:5000`，可传第一个参数覆盖，例如
  `python scripts\smoke_test.py http://127.0.0.1:8000`、`python scripts\smoke_lms.py --fast http://127.0.0.1:8000`；
- 新增脚本请遵循同样的命名：`seed_*` 灌数据、`smoke_*` 自检、`check_*` 静态检查。