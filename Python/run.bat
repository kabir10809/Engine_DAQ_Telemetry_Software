@echo off
rem Start the application on Windows from any folder.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 launch_engine_daq.py %* & exit /b)
where python >nul 2>nul && (python launch_engine_daq.py %* & exit /b)
echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
pause
