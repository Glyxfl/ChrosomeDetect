@echo off
cd /d "%~dp0"
set "CHROMOSOME_PYTHON=E:\Environment\minconda\envs\helix\python.exe"
if not exist "%CHROMOSOME_PYTHON%" set "CHROMOSOME_PYTHON=python"
echo Open http://127.0.0.1:8766 in your browser after the server starts.
"%CHROMOSOME_PYTHON%" -m workbench.server --port 8766
pause
