@echo off
REM Quick launcher for intelligent-audio-encoder GUI (Windows).
REM Usage: double-click, or: start_gui.bat [--backend cuda --gpu 0]
setlocal
cd /d "%~dp0"
if defined PYTHONPATH (set "PYTHONPATH=%~dp0;%PYTHONPATH%") else (set "PYTHONPATH=%~dp0")
set "PATH=C:\ffmpeg\bin;%PATH%"
set "PYTHONIOENCODING=utf-8"
where python >nul 2>nul || (echo ERROR: python not found on PATH. Install Python 3.11+ from python.org. & pause & exit /b 1)
where ffmpeg >nul 2>nul || echo WARNING: ffmpeg not found. Run tools\fetch_ffmpeg.ps1 and tools\fetch_tools.ps1 first.
if exist tools\bin\exhale\exhale.exe (echo [ok] exhale found) else (echo [!!] exhale missing - run tools\fetch_tools.ps1)
if exist tools\bin\fdkaac\fdkaac.exe (echo [ok] fdkaac found) else (echo [!!] fdkaac missing - run tools\fetch_tools.ps1)
if exist .venv\Scripts\python.exe (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
%PY% -m gui.main_window %*
if errorlevel 1 pause
