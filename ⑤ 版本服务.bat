@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title 网文工作站 · 版本服务

where python >nul 2>nul
if errorlevel 1 (
  echo   没找到 Python，先装一个 3.9+。
  echo   本项目只用标准库，不需要 pip install 任何东西。
  pause
  exit /b 1
)

echo   作用：页面左侧「版本控制」里的检查更新 / 立即更新 / 自动更新，
echo         都由这个窗口执行。容器里没有 git 和 docker 权限，只能由宿主机来干。
echo.
echo   页面上的按钮经看板后端转给这里，所以在哪台电脑打开都能点。
echo   为此必须带 --lan 监听所有网卡（容器走的是虚拟网关，不是 127.0.0.1）。
echo   Windows 第一次可能会弹防火墙提示，允许即可；端口不用对外开放。
echo.
echo   窗口关掉服务就停了。想开机自启：python dev\agent.py --lan --install
echo.

python dev\agent.py --lan %*
if errorlevel 1 (
  echo.
  echo   服务退出了，退出码 %errorlevel%，上面写了原因。
  pause
)
