@echo off
rem Abre o PC Monitor como administrador (necessario para ler as temperaturas)
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo Rode instalar.bat primeiro.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
