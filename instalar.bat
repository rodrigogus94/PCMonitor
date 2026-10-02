@echo off
rem Instala o PC Monitor (cria ambiente virtual, instala dependencias e baixa o LibreHardwareMonitor)
cd /d "%~dp0"
echo === Instalando PC Monitor ===
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version || goto erro
%PY% -m venv .venv || goto erro
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt || goto erro
python setup_lhm.py
echo.
echo Instalacao concluida. Para abrir, de dois cliques em executar.bat
pause
exit /b 0

:erro
echo.
echo Falha na instalacao. Confira se o Python 3.10 a 3.13 esta instalado (python.org) com "Add Python to PATH".
pause
exit /b 1
