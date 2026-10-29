# legacy/ —— 已被取代的旧模块（不参与运行，仅供追溯）

这些文件**没有被平台任何代码引用**，删掉也不影响启动。保留它们的原因只有一个：
如果想追溯"第一版是怎么做的"，还能看到当时的实现。

| 文件 | 原位置 | 曾经的作用 | 为什么被取代 |
| --- | --- | --- | --- |
| `rag_qa.py` | `backend/` | 基于 langchain + FAISS 的问答原型，硬编码 `models/llm/qwen--Qwen2.5-7B-Instruct` 路径 | 由 `backend/kb_service.py`（自研分块 + 混合检索 + 引用格式化）取代；当前检索不依赖 langchain / FAISS |
| `build_knowledge_base.py` | `backend/` | 用 sentence-transformers 建 FAISS 向量库，产物为 `sources.pkl / texts.pkl / vectors.npy` | 由 `kb_service.py` 的索引（`kb_chunks.json / kb_vectors.npy / kb_index.json`）取代，零重依赖、可增量重建 |
| `create_tables.py` | `backend/` | 手工给 `users` 表补 `student_number / employee_number` 列 | 由 `backend/app.py` 启动时的自动迁移（`_migrate_schema`）取代 |
| `test_knowledge_base.py` | `backend/` | 手写的 FAISS 检索冒烟脚本 | 由 `scripts/smoke_test.py` 的「知识库检索与引用」小节取代 |
| `test_system.py` | `backend/` | 早期 Qwen RAG 系统的 HTTP 自测 | 由 `scripts/smoke_test.py`（36 项）与 `scripts/smoke_lms.py`（27 项）取代 |

## 注意

- 这些文件里有 `import torch` / `transformers` / `langchain_community` / `sentence_transformers` 等模块级导入，
  **在当前依赖清单下直接运行会报错**——它们本来就不是运行路径的一部分。
- `rag_qa.py` 与 `build_knowledge_base.py` 里写死的 `models/llm/qwen--Qwen2.5-7B-Instruct` 目录已随纯文本权重一并删除
  （见 `models/README.md`）。
- 如果确认不需要追溯，整个目录可以直接删掉：

```powershell
Remove-Item -Recurse -Force legacy
```