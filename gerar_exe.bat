@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if not exist ".venv\Scripts\python.exe" (
  echo Preparando ambiente Python 3.13 para compilacao local...
  py -3.13 -m venv .venv
  if errorlevel 1 (
    echo Erro: instale Python 3.13 e tente novamente.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto error
set "PATH=%CD%\.venv\Scripts;%PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
if errorlevel 1 goto error
echo Arquivos gerados na pasta release.
pause
exit /b 0
:error
echo Nao foi possivel compilar. Consulte o erro acima.
pause
exit /b 1
