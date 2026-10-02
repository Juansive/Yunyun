@echo off
rem Double-click to build Yunyun into the dist folder (see tools\build_exe.py).
cd /d "%~dp0.."
".venv\Scripts\python.exe" tools\build_exe.py
pause
