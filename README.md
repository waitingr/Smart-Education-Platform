# Qwen RAG System - 重新运行指南

## 项目概述
这是一个基于Qwen 2.5大模型的智能教学平台，包含RAG（检索增强生成）功能和Web前端界面。

## 当前目录结构
```text
项目根目录/
├── backend/                  # Flask 后端
│   ├── app.py
│   ├── routes.py
│   ├── config.py
│   ├── models.py
│   ├── rag_service.py
│   └── ...
├── frontend/                 # 前端页面
│   ├── index.html
│   ├── css/
│   └── js/
├── models/                   # 模型目录
│   ├── llm/
│   ├── embedding_model/
│   └── ...
├── data/                     # 运行时资源目录
│   ├── vector_db/
│   ├── knowledge_base_docs/
│   ├── video/
│   ├── materials/
│   └── attachments/
├── README.md
└── .env
```

## 系统组件
1. **Flask后端** - 提供API接口，处理用户请求
2. **RAG服务** - 实现知识库检索和问答功能
3. **Web前端** - 用户交互界面
4. **模型文件** - Qwen 2.5-7B-Instruct和Embedding模型

## 运行步骤

### 1. 安装依赖
在项目根目录执行：
```bash
cd backend
pip install -r requirements.txt
```

### 2. 启动后端
```bash
cd backend
python app.py
```

### 3. 打开前端页面
在浏览器中直接打开：
```text
frontend/index.html
```

## 访问地址
- **后端API**: http://localhost:5000
- **前端界面**: 直接打开 frontend/index.html 文件

## 主要功能
1. **用户注册/登录** - 支持用户账户管理
2. **智能问答** - 基于知识库的问答功能
3. **代码评测** - 代码相关的问题解答
4. **知识库管理** - 支持文档上传和知识库构建

## 已修复的问题
1. ✅ 路径配置错误 - 已更新所有路径为正确位置
2. ✅ 编码问题 - 修复了中文显示和emoji字符问题
3. ✅ 依赖包安装 - 已安装所有必需的Python包
4. ✅ 模型路径 - 更新为本地模型路径
5. ✅ CUDA配置 - 改为CPU模式运行

## 注意事项
1. 系统运行在CPU模式，首次加载模型可能需要较长时间
2. 确保MySQL数据库已配置（在backend/.env中）
3. 如果遇到模型加载问题，请检查模型文件完整性

## 故障排除
1. **后端无法启动**：检查端口5000是否被占用
2. **模型加载失败**：确认模型文件路径正确
3. **前端无法连接后端**：检查CORS配置和网络连接

## 文件结构
```
D:\Dev_Environment\MiniConda3\CondaEnvironment\qwen_env\LLM_Module\
├── backend/              # Flask后端
│   ├── app.py           # 主应用
│   ├── routes.py        # 路由
│   ├── config.py        # 配置
│   └── .env             # 环境变量
├── models/              # 模型文件
├── embedding_model/     # 嵌入模型
├── vector_db/           # 向量数据库
├── knowledge_base_docs/ # 知识库文档
├── rag_service.py       # RAG服务
├── build_knowledge_base.py # 知识库构建工具
├── index.html          # 前端页面
└── start_system.bat     # 启动脚本
```

## 技术栈
- **后端**: Flask, SQLAlchemy, PyMySQL
- **模型**: Qwen 2.5-7B-Instruct, Embedding模型
- **前端**: HTML, CSS, JavaScript, TailwindCSS
- **向量数据库**: FAISS, NumPy