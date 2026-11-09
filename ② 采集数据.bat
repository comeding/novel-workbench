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
echo   采集中，别去动模拟器。中途要停就按 Ctrl+C。
echo   模拟器没开的话脚本会自己拉起来，采完自动入库，看板刷新就是新数据。
echo   只想采一个榜：② 采集数据.bat 会员榜
echo.

python collector\collect.py %*
echo.
if errorlevel 1 (
  echo [x] 采集没成功，按上面的提示处理后重试。
) else (
  echo [ok] 采集完成。
)
pause
