@echo off
cd /d "%~dp0"
python setup_analysis.py
if errorlevel 1 goto failed
echo Analysis setup complete. Run start.cmd to open COACHDESK.
pause
exit /b 0
:failed
echo Analysis setup failed. Please review the message above.
pause
exit /b 1
