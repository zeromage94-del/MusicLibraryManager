@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente Python nao encontrado. Execute instalar.bat.
  pause
  exit /b 1
)
echo Iniciando Music Library Manager com log de diagnostico...
".venv\Scripts\python.exe" "app.py" 1>"inicializacao.log" 2>&1
set EXIT_CODE=%ERRORLEVEL%
echo.
echo Aplicativo encerrado com codigo %EXIT_CODE%.
echo Caso tenha ocorrido erro, abra inicializacao.log nesta pasta.
pause
exit /b %EXIT_CODE%
