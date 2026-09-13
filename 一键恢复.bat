@echo off
chcp 65001 >nul
title OpenClaw 一键恢复
echo ============================================
echo   OpenClaw 本地 AI 环境一键恢复
echo   重装系统后双击本文件即可
echo ============================================
echo.
"L:\OpenClaw\python\python.exe" "L:\OpenClaw\OpenClawData\console\restore_env.py"
echo.
pause
