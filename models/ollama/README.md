# models/ollama/ —— 项目自带的 Ollama 运行时

这里放的是**推理服务本体**和**模型库**，都随项目走，不依赖 `E:\Tools` 或 `%USERPROFILE%\.ollama`。

```text
models/ollama/
├── runtime/                 # 可执行部分（1.2 GB）
│   ├── ollama.exe
│   └── lib/ollama/          # llamacpp 后端 + CUDA v12/v13 + vulkan 运行库
├── store/                   # 模型库（= OLLAMA_MODELS，5.4 GB）
│   ├── blobs/               # 权重分片：qwen3-vl:8b = 4.68 GB 模型 + 0.70 GB mmproj
│   ├── manifests-v2/
│   └── metadata/
└── ollama.cmd               # 带环境变量的便捷入口
```

## 正确的启动方式

```powershell
# 从项目根目录
models\ollama\ollama.cmd serve           # 前台启动推理服务
models\ollama\ollama.cmd list            # 另一个窗口查看模型
```

`ollama.cmd` 等价于：

```bat
set OLLAMA_HOST=127.0.0.1:11434
set OLLAMA_MODELS=%~dp0store
set OLLAMA_KEEP_ALIVE=30m
set OLLAMA_MAX_LOADED_MODELS=1
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_FLASH_ATTENTION=1
%~dp0runtime\ollama.exe %*
```

## 为什么 exe 放在 runtime/ 而模型放在 store/

| 目录 | 性质 | 换机器时 |
| --- | --- | --- |
| `runtime/` | 与操作系统/架构绑定（Windows x64 二进制、CUDA 运行库） | 换平台要换成对应版本 |
| `store/` | 纯数据（GGUF 权重 + 清单 + 元数据） | 通用，可整个拷走 |

分开之后职责清楚：要升级 Ollama 只换 `runtime/`，要换模型只动 `store/`，互不影响。

## 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Error: model not found` / `/api/tags` 为空 | 启动时没设 `OLLAMA_MODELS`，去默认目录找了 | 用 `ollama.cmd` 或 `.\run.ps1` 启动 |
| `bind: Only one usage of each socket address` | 已有 ollama 在跑 | `Get-Process ollama* \| Stop-Process -Force` 后重试 |
| 加载后显存爆掉 | 同时驻留多个模型 / 并行请求 | 已固定 `MAX_LOADED_MODELS=1`、`NUM_PARALLEL=1` |
| 首次调用很慢（~20 s） | 模型冷启动 | 正常；`KEEP_ALIVE=30m` 之后不再重复加载 |
| 想确认模型真的在项目里 | `ollama.cmd list` 输出的路径无关，用 `/api/v1/health/stack` 看 `llm.model` | — |