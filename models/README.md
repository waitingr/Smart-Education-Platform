# models/ —— 本地推理运行时与模型（项目自带，不依赖 E:\Tools）

本目录**已包含运行所需的全部内容**，把整个项目文件夹拷到另一台机器即可直接跑（不需要另装 Ollama）。
它是 Git 忽略的：体积约 6.6 GB，不进版本库。

```text
models/
├── README.md                  # 本文件
├── ollama/
│   ├── runtime/               # Ollama 可执行部分（2.1 GB）
│   │   ├── ollama.exe         # 推理服务本体
│   │   └── lib/ollama/        # llamacpp 后端 + CUDA/vulkan 运行库
│   ├── store/                 # 模型库 OLLAMA_MODELS（5.4 GB）
│   │   ├── blobs/             # 权重分片（qwen3-vl:8b 的 4.68 GB 模型 + 0.70 GB 视觉投影层）
│   │   ├── manifests-v2/      # 模型清单
│   │   └── metadata/          # 张量元数据
│   ├── ollama.cmd             # 便捷入口：带上环境变量再转发参数，如 ollama.cmd list
│   └── README.md              # 这一层的启动方式与故障排查
├── llm/                       # （空）微调基座权重放这里，默认不下载
└── .gitkeep
```

## 现在装的是什么

| 项 | 值 |
| --- | --- |
| 模型 | `qwen3-vl:8b`（Q4_K_M + Q8 mmproj，5.38 GB） |
| 能力 | 文本生成 + **直接看图**（同一模型，不用另配 OCR） |
| 运行时 | Ollama 0.40.12（`models/ollama/runtime/ollama.exe`） |
| 模型库路径 | `models/ollama/store`（由 `OLLAMA_MODELS` 指定） |
| 显存占用 | 约 7.5 / 8.1 GiB（RTX 4060 Laptop 8 GB 实测） |

## 启动方式（二选一）

```powershell
# 方式一：项目根目录一键启动（推荐，会自动拉起 Ollama 并检查模型）
.\run.ps1

# 方式二：只起推理服务
models\ollama\ollama.cmd serve
models\ollama\ollama.cmd list        # 另开一个窗口
```

> 关键：`OLLAMA_MODELS` 必须指向 `models\ollama\store`。脚本（`run.ps1` / `run.sh` /
> `scripts\start_ollama.ps1` / `models\ollama\ollama.cmd`）都会自动设置；
> 手动直接双击 `ollama.exe` 则**不会**——它会去 `%USERPROFILE%\.ollama\models` 找模型，
> 表现为"模型明明在这儿却提示没有"。

## 常见操作

| 目的 | 命令 |
| --- | --- |
| 查看已装模型 | `models\ollama\ollama.cmd list` |
| 查看模型是否驻留显存 | `models\ollama\ollama.cmd ps` |
| 再拉一个模型 | `models\ollama\ollama.cmd pull qwen3-vl:4b`（低显存备选） |
| 删模型 | `models\ollama\ollama.cmd rm qwen3-vl:4b` |
| 整体重装 | 删掉 `models/ollama` 后重新解压 Ollama 并 `pull qwen3-vl:8b` |

## 为什么把模型放进项目

| 原因 | 说明 |
| --- | --- |
| 可搬运 | 比赛现场/评审机器通常不允许联网，"拷文件夹就能跑"比"先装 Ollama 再 pull 模型"可靠得多 |
| 可复现 | 版本（0.40.12）、参数（`num_ctx=8192`）、权重（Q4_K_M + Q8 mmproj）全部固定，不受机器上别处的环境影响 |
| 可解释 | 体积去向一目了然：代码 13 MB + 运行时 1.2 GB + 权重 5.4 GB |
| 不污染系统 | 不再往 `E:\Tools`、`%USERPROFILE%\.ollama` 里散落文件，卸载=删文件夹 |

## 注意事项

- **不要**把本目录提交到 Git（已在 `.gitignore` 中忽略）；提交的是 `README.md` 与源码。
- 换机器时如果显卡不同，Ollama 会自动选择 CPU / CUDA / Vulkan 后端，无需改配置；
  显存 < 8 GB 换 `qwen3-vl:4b`（`README.md` 与 `docs/03` 有说明）。
- 目录被移动过之后，务必用项目脚本启动（它们会重新指向正确的 `OLLAMA_MODELS`）。