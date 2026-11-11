@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title 网文工作站 · 更新代码

where python >nul 2>nul
if errorlevel 1 (
  echo   没找到 Python，先装一个 3.9+。
  echo   本项目只用标准库，不需要 pip install 任何东西。
  pause
  exit /b 1
)

python dev\update.py %*
set RC=%errorlevel%

echo.
if not "%RC%"=="0" (
  echo   没走完，退出码 %RC%，上面写了原因。
)
pause
exit /b %RC%
