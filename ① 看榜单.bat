@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title 网文工作站 · 启动并抓取

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

echo   启动服务…库里没数据或数据超过 6 小时，会自动去 QQ阅读 抓一轮，
echo   页面会显示采集进度（约 10 秒），不用另外点。窗口别关。
echo.
python backend\server.py
if errorlevel 1 (
  echo.
  echo   服务没起来，看上面的报错。
  pause
)
