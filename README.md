# OpenClaw 控制台

管理本地 AI 环境的图形界面：llama 模型服务 + ComfyUI 生图 + OpenClaw 网关，一键启动/停止/恢复。

## 功能
- 模型 / ComfyUI / 网关 启停控制 + 状态灯
- 生图默认模型、默认 LoRA（Krea2 / Z-Image 独立配置）下拉选择
- 清理内存 / 显存、上传/生成文件夹快捷打开
- 硬件检测 → 自动推荐 llama 参数 → 一键应用
- 一键恢复：检查并拉起缺失服务
- 开机自启：模型 / 网关 / ComfyUI
- 重装系统后双击「一键恢复.bat」全部拉起

## 文件
- OpenClaw控制台_v2.2.exe — Windows 单文件程序（双击运行）
- openclaw_console.py — 控制台源码
- comfyui_mcp_server.py — 生图 MCP 服务
- 
estore_env.py — 一键恢复核心
- 一键恢复.bat — 重装系统入口
- 发布说明.md — 目录结构、恢复步骤、版本记录

## 环境依赖（运行时）
- llama-server + GGUF 模型（对话推理）
- ComfyUI（生图）
- Node.js 24+（网关，可用便携版免安装）

详细说明见「发布说明.md」。
