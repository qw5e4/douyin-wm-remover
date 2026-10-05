@echo off
cd /d "%~dp0"
REM 优先使用仓库内虚拟环境，否则回退系统 python
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe gui/main.py
) else (
  python gui/main.py
)
if errorlevel 1 pause
