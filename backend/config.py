"""集中配置。

设计原则（对应赛事「可复制、可推广」「可复现」要求）：
1. 数据库：默认 MySQL；若本机 MySQL 不可达，自动降级到 SQLite（并打印显著警告），
   保证任何评审机器 clone 后都能一键跑起来。可用 DATABASE_URL 显式覆盖。
2. 大模型：auto 模式按「本地 Qwen → 讯飞星火/OpenAI 兼容接口 → 离线演示引擎」
   依次降级。所有重型依赖（torch/transformers）均为惰性导入，缺失时平台仍可完整运行。
3. 学科：DISCIPLINE_* 可通过环境变量替换，同一套代码可迁移到其它学科（可复制性）。
"""
import os
import socket

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
# 环境变量文件：根目录 .env 为规范位置，backend/.env 作为兼容回退（不覆盖已设置的值）
for _env_file in (os.path.join(PROJECT_ROOT, '.env'), os.path.join(BASE_DIR, '.env')):
    if os.path.isfile(_env_file):
        load_dotenv(_env_file, override=False)


def _flag(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _mysql_uri():
    user = os.getenv('MYSQL_USER')
    if not user:
        return None
    password = os.getenv('MYSQL_PASSWORD', '')
    host = os.getenv('MYSQL_HOST', 'localhost')
    port = os.getenv('MYSQL_PORT', '3306')
    database = os.getenv('MYSQL_DATABASE', 'qwen_db')
    return f'mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4'


def _mysql_reachable(timeout=1.5):
    host = os.getenv('MYSQL_HOST', 'localhost')
    port = _int('MYSQL_PORT', 3306)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _database_uri():
    explicit = os.getenv('DATABASE_URL')
    if explicit:
        explicit = explicit.strip()
        # 支持 DATABASE_URL=sqlite:///data/smart_edu.db 这种相对写法（相对项目根目录），
        # 便于在任意机器上复现而不用把绝对路径写死。
        prefix = 'sqlite:///'
        if explicit.startswith(prefix):
            path = explicit[len(prefix):]
            if path and not os.path.isabs(path) and not path.startswith(':'):
                resolved = os.path.join(PROJECT_ROOT, path.replace('/', os.sep))
                os.makedirs(os.path.dirname(resolved), exist_ok=True)
                return prefix + resolved
        return explicit
    mysql = _mysql_uri()
    if mysql and (not _flag('DB_AUTO_FALLBACK_SQLITE', True) or _mysql_reachable()):
        return mysql
    fallback_path = os.path.join(DATA_DIR, 'smart_edu.db')
    os.makedirs(os.path.dirname(fallback_path), exist_ok=True)
    if mysql:
        print('=' * 72)
        print('[配置警告] 未检测到可用的 MySQL 服务，已自动降级为本地 SQLite：')
        print(f'           {fallback_path}')
        print('           如需使用 MySQL，请启动服务并确认 backend/.env 配置；')
        print('           或设置 DATABASE_URL 指定任意数据库。')
        print('=' * 72)
    return f'sqlite:///{fallback_path}'


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')

    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    if SQLALCHEMY_DATABASE_URI.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_size': 10,
            'pool_timeout': 30,
            'pool_recycle': 1800,
            'pool_pre_ping': True,
            'echo': False,
        }

    # ---------------- 学科定位 ----------------
    DISCIPLINE_NAME = os.getenv('DISCIPLINE_NAME', '计算机科学与技术')
    DISCIPLINE_COURSE = os.getenv('DISCIPLINE_COURSE', '数据结构与算法')
    DISCIPLINE_ALIAS = os.getenv('DISCIPLINE_ALIAS', '计算机、软件工程、数据结构、算法、程序设计')
    PLATFORM_NAME = os.getenv('PLATFORM_NAME', '灵眸智教')
    AI_CONTENT_LABEL = '本内容由 AI 生成，已经过学科知识库检索增强，仅供教学与科研参考'

    # ---------------- 大模型 ----------------
    # auto | local | ollama | spark | openai | offline
    # ollama = 本机 Ollama 的视觉大模型（默认 qwen3-vl:8b），既能对话也能直接看图
    LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'auto').strip().lower()
    LLM_MODEL_PATH = os.getenv('LLM_MODEL_PATH', '').strip()
    LLM_MAX_NEW_TOKENS = _int('LLM_MAX_NEW_TOKENS', 1024)
    LLM_TEMPERATURE = _float('LLM_TEMPERATURE', 0.35)
    LLM_TOP_P = _float('LLM_TOP_P', 0.9)
    LLM_TIMEOUT = _int('LLM_TIMEOUT', 120)

    # 讯飞星火（Spark）或任意 OpenAI 兼容网关
    SPARK_APP_ID = os.getenv('SPARK_APP_ID', '').strip()
    SPARK_API_SECRET = os.getenv('SPARK_API_SECRET', '').strip()
    SPARK_API_KEY = os.getenv('SPARK_API_KEY', '').strip()
    SPARK_MODEL = os.getenv('SPARK_MODEL', '4.0Ultra').strip()
    LLM_BASE_URL = os.getenv('LLM_BASE_URL', '').strip().rstrip('/')
    LLM_API_KEY = os.getenv('LLM_API_KEY', '').strip()
    LLM_MODEL_NAME = os.getenv('LLM_MODEL_NAME', 'generalv3.5').strip()
    # 本机模型上下文预算：qwen3-vl:8b 的 num_ctx 固定 8192，正文超过预算必须截断而不是静默丢内容
    LLM_CONTEXT_CHARS = _int('LLM_CONTEXT_CHARS', 6000)
    # 是否允许把图片直接喂给生成模型（本机视觉模型 / 图像理解网关）
    LLM_VISION = os.getenv('LLM_VISION', 'auto').strip().lower()

    # ---------------- 视觉模型（图片理解） ----------------
    # ollama | openai | off：ollama 指本机 Ollama，默认模型 qwen3-vl:8b（8GB 显存可跑）
    VISION_PROVIDER = os.getenv('VISION_PROVIDER', 'auto').strip().lower()
    VISION_BASE_URL = os.getenv('VISION_BASE_URL', 'http://127.0.0.1:11434').strip().rstrip('/')
    VISION_MODEL = os.getenv('VISION_MODEL', 'qwen3-vl:8b').strip()
    VISION_TIMEOUT = _int('VISION_TIMEOUT', 120)
    # 视觉请求里的 keep_alive：让 8B 模型常驻，避免每次冷启动 ~20 秒（Ollama 侧同名环境变量为兜底）
    VISION_KEEP_ALIVE = os.getenv('VISION_KEEP_ALIVE', '30m').strip()
    VISION_MAX_IMAGE_SIDE = _int('VISION_MAX_IMAGE_SIDE', 1600)
    VISION_PROMPT = os.getenv(
        'VISION_PROMPT',
        '请用简体中文完整描述这张图片的内容。若图片包含文字、公式、代码或表格，请逐字转写并保持原有结构'
        '（代码放进代码块，表格用 Markdown 表格）。看不清的部分写「不清晰」，不要猜测。'
    )

    # 选了本机 Ollama 当生成模型时，接口地址与模型名默认复用视觉模型的配置
    if LLM_PROVIDER == 'ollama':
        LLM_BASE_URL = LLM_BASE_URL or f'{VISION_BASE_URL}/v1'
        LLM_API_KEY = LLM_API_KEY or 'ollama'
        if not os.getenv('LLM_MODEL_NAME'):
            LLM_MODEL_NAME = VISION_MODEL

    # ---------------- 向量/检索 ----------------
    # auto | sentence-transformers | transformers | hash（纯 numpy，无额外依赖）
    EMBEDDING_BACKEND = os.getenv('EMBEDDING_BACKEND', 'auto').strip().lower()
    EMBEDDING_MODEL_PATH = os.getenv('EMBEDDING_MODEL_PATH', '').strip()
    KB_TOP_K = _int('KB_TOP_K', 5)
    # 检索引擎：auto（有 C++ 内核就用）/ python / cpp
    KB_ENGINE = os.getenv('KB_ENGINE', 'auto').strip().lower()
    KB_MAX_CHUNK_CHARS = _int('KB_MAX_CHUNK_CHARS', 700)
    KB_CACHE_DIR = os.path.join(DATA_DIR, 'vector_db')
    KB_DOC_DIR = os.path.join(DATA_DIR, 'knowledge_base_docs')

    # ---------------- 文件 ----------------
    UPLOAD_DIR = os.path.join(DATA_DIR, 'attachments')
    VIDEO_DIR = os.path.join(DATA_DIR, 'video')
    MATERIAL_DIR = os.path.join(DATA_DIR, 'materials')
    MAX_ATTACHMENT_BYTES = _int('MAX_ATTACHMENT_BYTES', 20 * 1024 * 1024)
    MAX_TEXT_CHARS = _int('MAX_TEXT_CHARS', 30000)

    # ---------------- 运营/合规 ----------------
    EVAL_DIR = os.path.join(PROJECT_ROOT, 'data', 'eval')
    FEEDBACK_MIN_USERS = _int('FEEDBACK_MIN_USERS', 2)