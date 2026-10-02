@echo off
rem Lista todos os sensores que o programa consegue ler (use para diagnosticar temperaturas ausentes)
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
".venv\Scripts\python.exe" sensors.py > sensores.txt 2>&1
type sensores.txt
echo.
echo Resultado salvo em sensores.txt
pause
