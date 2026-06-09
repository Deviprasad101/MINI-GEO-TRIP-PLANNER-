@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv
  ".venv\Scripts\pip.exe" install -r requirements.txt
)
echo Starting GeoTrip Planner at http://127.0.0.1:5000
".venv\Scripts\python.exe" app.py
pause
