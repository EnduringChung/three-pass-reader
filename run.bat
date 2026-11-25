@echo off
rem Same as the desktop icon: start the reader hidden, open the browser, exit.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Create the environment first:  python -m venv .venv  ^&^&  .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "launcher.py"
