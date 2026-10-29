# data/ —— 数据目录

| 路径 | 内容 | 是否进版本库 | 说明 |
| --- | --- | --- | --- |
| `smart_edu.db` | SQLite 演示库（用户 / 作业 / 会话 / 课程空间 12 张表） | 否（`.gitignore`） | 由 `scripts/seed_demo.py` + `scripts/seed_lms.py` 生成，删掉后重新灌即可 |
| `knowledge_base_docs/` | 学科知识库**源文档**（教材讲义 / 课程标准 / 行业标准 / 论文 / 案例），11 篇 | 是 | 平台启动时据此建索引；新增文档放这里后调用 `/api/v1/kb/rebuild` 或在界面上"重建索引" |
| `vector_db/` | 知识库索引缓存：`kb_chunks.json` / `kb_vectors.npy` / `kb_index.json` | 否 | 由 `backend/kb_service.py` 生成，可随时重建（这也是"换学科只换知识库"的落点） |
| `eval/testset.jsonl` | 12 题标准测试集（含参考答案、关键词、期望引用） | 是 | `scripts/smoke_test.py` 与界面的"评测"都读它 |
| `eval/reports/` | 每次评测落盘的 Markdown 报告 | 否 | 文件名带时间戳，便于对比不同模型/版本 |
| `attachments/` | 用户与自检脚本上传的附件（图片 / 文档） | 否 | 上传即入库，附件内容同时进入检索上下文 |
| `materials/` | 教学素材（含 `.docx` 示例） | 部分（商业计划书已忽略） | 演示用 |
| `video/` | 视频素材占位 | 否 | 目前仅保留 `.gitkeep` |

## 关于演示数据的诚实说明

- 学生 55 人、作业 4 次、提交 196 份：由 `scripts/seed_demo.py` **程序生成**；
- 课程空间 1 门课 / 8 章 / 7 个任务点 / 40 条作答 / 6 次签到 / 3 条通知：由 `scripts/seed_lms.py` **程序生成**；
- 同一学号、同一随机种子下结果**可复现**，用于验证统计口径与流程，**不代表真实教学行为**；
- 换真实数据：把 `seed_demo.py` / `seed_lms.py` 换成学校的账号与成绩导入即可，看板与报告不用改。

## 数据库文件在哪

平台按 `DATABASE_URL` → MySQL → SQLite 的顺序选择数据源：
`backend/.env` 里当前固定为 `DATABASE_URL=sqlite:///data/smart_edu.db`（相对路径按项目根目录解析），
所以演示数据永远落在本文件的 `data/smart_edu.db`，不会因为本机装了 MySQL 而"数据跑偏"。