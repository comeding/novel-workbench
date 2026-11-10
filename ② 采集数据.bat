@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title 网文工作站 · 采集数据

where python >nul 2>nul
if errorlevel 1 (
  echo   没找到 Python，先装一个 3.9+。
  pause
  exit /b 1
)

echo.
echo   直接请求 QQ阅读 榜单接口，不用模拟器，一轮约 10 秒。
echo   中途要停就按 Ctrl+C。采完自动入库，看板刷新就是新数据。
echo   只想采一个榜：② 采集数据.bat 会员榜
echo.

python collector\api_rank.py %*
echo.
if errorlevel 1 (
  echo [x] 采集没成功，先跑「③ 环境自检.bat」看是哪一步不通。
) else (
  echo [ok] 采集完成。
)
pause
