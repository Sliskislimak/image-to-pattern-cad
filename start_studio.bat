@echo off
title Image-to-Pattern CAD Studio
echo ===============================================================================
echo Uruchamianie Image-to-Pattern CAD Studio...
echo ===============================================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_server.py %*
) else (
    python run_server.py %*
)
pause
