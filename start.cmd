@echo off
rem condopt 工作台 · 一键启动（只在本机 127.0.0.1 上跑，不发外网请求）
rem 用法：双击本文件，或在仓库根目录执行 start.cmd
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem 默认直接指向仓库现有的 runs/，打开就能看到 runs\demo 这类样例 study。
rem 想用工作台自带的独立目录，把下面这行换成：--runs-dir state\runs
py -3 server.py --port 8770 --open --runs-dir state\runs
if errorlevel 1 (
  echo.
  echo [工作台] 启动失败。请确认已安装 Python 3.10+ 并能在命令行执行 py -3 --version
  pause
)
endlocal
