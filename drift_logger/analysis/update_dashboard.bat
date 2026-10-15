@echo off
rem Analyse les nouveaux enregistrements et ouvre le tableau de bord.
rem Analyses new recordings and opens the dashboard.
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% --version >nul 2>nul || (
  echo Python est introuvable. Installe-le depuis https://www.python.org en cochant "Add python.exe to PATH".
  pause
  exit /b 1
)
%PY% -c "import numpy, pandas" >nul 2>nul || %PY% -m pip install --user -r requirements.txt
%PY% analyse.py || (pause & exit /b 1)
%PY% build_dashboard.py || (pause & exit /b 1)
start "" "%~dp0dashboard.html"
