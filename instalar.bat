@echo off
chcp 65001 >nul
title Instalar Music Library Manager
cd /d "%~dp0"
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if errorlevel 1 (
  echo ERRO: Instale Python 3.11 ou superior em python.org e execute novamente.
  pause
  exit /b 1
)
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo.
  echo ATENCAO: FFmpeg nao encontrado no PATH.
  echo Instale FFmpeg pelo winget: winget install --id Gyan.FFmpeg -e
  echo Feche e abra o terminal novamente apos instalar.
)
echo.
echo Instalacao finalizada! Execute iniciar.bat.
pause
exit /b 0
:fail
echo.
echo ERRO na instalacao. Verifique sua conexao e versao do Python.
pause
exit /b 1
