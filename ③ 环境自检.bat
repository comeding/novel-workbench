@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title 网文工作站 · 环境自检

where python >nul 2>nul
if errorlevel 1 (
  echo   没找到 Python，先装一个 3.9+。
  echo   本项目只用标准库，不需要 pip install 任何东西。
  pause
  exit /b 1
)

python collector\collect.py --check
echo.
pause
