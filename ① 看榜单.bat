@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title 网文工作站 · 看榜单

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   没找到 Python。装一个 3.9 以上的版本再回来。
  echo   下载：https://www.python.org/downloads/
  echo   安装时记得勾选 Add Python to PATH。
  echo.
  pause
  exit /b 1
)

python backend\server.py
if errorlevel 1 (
  echo.
  echo   服务没起来，看上面的报错。
  pause
)
