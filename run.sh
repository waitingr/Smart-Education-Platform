#!/usr/bin/env bash
# ============================================================================
# 灵眸智教 · 一键启动（Linux / macOS）
# 用法：  ./run.sh            启动（Ollama 未运行则尝试拉起）
#         ./run.sh --check    只做环境自检
#         ./run.sh --seed     先灌演示数据再启动
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"
CHECK=0
SEED=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        --seed) SEED=1 ;;
    esac
done

cyan() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  OK  %s\033[0m\n' "$1"; }
warn() { printf '\033[33m   !  %s\033[0m\n' "$1"; }

cyan '环境检查'

if [ ! -x "$PY" ]; then
    warn "未找到虚拟环境：$PY"
    echo "     先执行： python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
    exit 1
fi
ok "Python: $PY"

if [ -f "$ROOT/.env" ] || [ -f "$ROOT/backend/.env" ]; then
    ok "配置文件已就位（.env / backend/.env）"
else
    warn '缺少 .env，将使用默认配置（SQLite + 离线检索模式）'
fi

ok "数据库: $("$PY" -c "import sys; sys.path.insert(0, '$ROOT/backend'); from config import Config; print(Config.SQLALCHEMY_DATABASE_URI)")"

export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_KEEP_ALIVE=30m
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_FLASH_ATTENTION=1

# 模型库与运行时都在项目内（Linux/macOS 版需自行放入对应平台的 Ollama 二进制）
if [ -d "$ROOT/models/ollama/store/blobs" ]; then
    export OLLAMA_MODELS="$ROOT/models/ollama/store"
fi
OLLAMA_BIN="$ROOT/models/ollama/runtime/ollama"
if [ ! -x "$OLLAMA_BIN" ]; then
    OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
fi

if [ -n "${OLLAMA_BIN:-}" ]; then
    if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama 已运行：$(curl -fsS http://127.0.0.1:11434/api/tags | "$PY" -c 'import json,sys; print(", ".join(m["name"] for m in json.load(sys.stdin)["models"]))')"
    elif [ "$CHECK" -eq 0 ]; then
        cyan '启动 Ollama'
        ("$OLLAMA_BIN" serve >/dev/null 2>&1 &) || true
        sleep 5
        curl -fsS --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
            && ok 'Ollama 已就绪' || warn 'Ollama 启动失败，将进入离线检索模式'
    fi
    if [ -d "$ROOT/models/ollama/store/blobs" ]; then
        ok "模型库: models/ollama/store"
    fi
else
    warn '未找到 Ollama：图片理解不可用，平台走离线检索模式（功能仍完整）'
fi

if [ -f "$ROOT/data/vector_db/kb_index.json" ]; then
    ok '知识库索引: data/vector_db'
else
    warn '知识库索引不存在，首次启动会自动构建'
fi

if [ "$CHECK" -eq 1 ]; then
    cyan '自检完成（未启动服务）'
    exit 0
fi

if [ "$SEED" -eq 1 ]; then
    cyan '灌入演示数据'
    "$PY" "$ROOT/scripts/seed_demo.py"
    "$PY" "$ROOT/scripts/seed_lms.py"
fi

cyan '启动后端（Ctrl+C 停止）'
echo '  前端入口：http://127.0.0.1:5000/'
cd "$ROOT/backend"
exec "$PY" app.py