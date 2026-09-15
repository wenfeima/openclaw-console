# OpenClaw 控制台

本地 AI 环境一站式管理控制台：管理 **llama.cpp（LLM 推理）**、**OpenClaw Gateway（18789）**、**ComfyUI（生图 8188）**，并打通 webchat / 微信通道的本地生图链路。

## 功能

- **模型管理**：一键启动/停止本地 LLM（llama-server）、生图模型（ComfyUI），模型目录跟随 `paths.json`
- **生图链路**：webchat / 微信中直接调用 ComfyUI 生图，图片自动投递显示（MCP 本地媒体链路）
- **生图模型 / LoRA / 工作流选择**：下拉选择即保存，MCP 实时读取
- **内存 / 显存清理**：生图完成后自动清理，节点可自定义
- **路径设置**：LLM 目录、ComfyUI 根目录等，改路径保存后自动重打媒体白名单补丁（重启网关生效）
- **一键恢复**：重装系统后改完路径即可复原环境
- **日志窗口**：ComfyUI / LLM / 网关日志集成、可磁吸、翻页

## 文件说明

| 文件 | 说明 |
|---|---|
| `OpenClaw控制台_v2.5.exe` | 主程序（Windows 单文件，无需安装） |
| `openclaw_console.py` | 控制台源码 |
| `comfyui_mcp_server.py` | ComfyUI MCP 服务器（网关内嵌加载，生图入口） |
| `apply_openclaw_patches.py` | **生图链路完全修复脚本**（幂等可重复执行） |
| `restore_env.py` | 环境一键恢复脚本 |
| `paths.json` | 路径配置（权威来源） |

## 使用

1. 双击 `OpenClaw控制台_v2.5.exe` 启动。
2. 首次使用在「设置」页配置 LLM 模型目录 / ComfyUI 根目录，点「保存路径并应用」。
3. 模型页一键启动服务；webchat / 微信中直接发消息生图。

## 修复脚本

OpenClaw 升级会把 `dist/*.mjs` 覆盖回原版，导致「生图成功但前端不显示图片」。运行：

```powershell
python apply_openclaw_patches.py
```

自动完成 4 处修复并重启网关：

1. 媒体白名单追加 `{comfy_root}\ComfyUI\output` 与 `\models`（**从 paths.json 动态读取**，改目录后重跑即更新）
2. `mcp-content` details 摊平 structuredContent（media 抬到 details 顶层）
3. `embedded-agent-tool-media` 放行 `trustedLocalMedia` 本地媒体
4. 校验 `comfyui_mcp_server.py` 返回结构

幂等：已打补丁自动跳过；重复执行安全。`--no-restart` 跳过网关重启。

## 重装系统恢复

1. 安装 Node.js（≥18），保持路径一致或修改 `paths.json`
2. 把 `OpenClawData` / `llama` / ComfyUI 放在原路径（或改 `paths.json`）
3. 启动控制台 → 设置页改路径 → 保存 → 模型页一键启动
