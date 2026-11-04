@echo off
cd /d "%~dp0"
python setup_local.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" setup_analysis.py --if-needed
if errorlevel 1 goto failed
".venv\Scripts\python.exe" run_local.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Please review the message above.
pause
exit /b 1
