@echo off
cd /d "%~dp0"
echo Starting GeoTrip Planner at http://127.0.0.1:5000
echo Open that URL in your browser (no Cursor port forward needed).
python app.py
pause
