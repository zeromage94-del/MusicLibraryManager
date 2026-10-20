@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Execute instalar.bat antes de iniciar.
  pause
  exit /b 1
)
start "Music Library Manager" ".venv\Scripts\pythonw.exe" "app.py"
