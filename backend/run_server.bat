@echo off
setlocal EnableDelayedExpansion
set "ROOT=%~dp0.."
cd /d "%ROOT%"

echo ========================================
echo   GeoTrip Planner - Server Startup
echo ========================================
echo.

REM --- PostgreSQL setup (Administrator only) ---
net session >nul 2>&1
if %errorlevel% equ 0 (
  call :SETUP_POSTGRES
) else (
  echo [INFO] Skipping PostgreSQL setup ^(not Administrator^).
  echo        If login/DB fails, right-click run_server.bat -^> Run as administrator.
  echo.
)

REM --- Python virtual environment ---
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv
  if errorlevel 1 (
    echo ERROR: Could not create .venv. Install Python 3.12+ and try again.
    pause
    exit /b 1
  )
  ".venv\Scripts\pip.exe" install -r backend\requirements.txt
  if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
  )
)

REM --- Read app port from backend\.env (default 5000) ---
set "APP_PORT=5000"
if exist "backend\.env" (
  for /f "usebackq tokens=1,* delims==" %%a in ("backend\.env") do (
    if /i "%%a"=="PORT" set "APP_PORT=%%b"
  )
)

echo Starting GeoTrip Planner at http://127.0.0.1:!APP_PORT!
echo Press Ctrl+C to stop the server.
echo.
".venv\Scripts\python.exe" app.py
pause
exit /b 0

:SETUP_POSTGRES
echo [INFO] Running PostgreSQL setup...
set "PGDATA=C:\Program Files\PostgreSQL\18\data"
set "PSQL=C:\Program Files\PostgreSQL\18\bin\psql.exe"
set "SERVICE=postgresql-x64-18"
set "NEWPASS=postgres"
set "DBNAME=geo trip planner"

if not exist "%PSQL%" (
  echo [WARN] PostgreSQL 18 not found at "%PSQL%". Skipping DB setup.
  exit /b 0
)

net stop %SERVICE%
copy /Y "%PGDATA%\pg_hba.conf" "%PGDATA%\pg_hba.conf.bak" >nul

powershell -NoProfile -Command ^
  "$c = Get-Content '%PGDATA%\pg_hba.conf';" ^
  "$c = $c -replace '(?m)^host\s+all\s+all\s+127\.0\.0\.1/32\s+\S+','host    all             all             127.0.0.1/32            trust';" ^
  "$c = $c -replace '(?m)^host\s+all\s+all\s+::1/128\s+\S+','host    all             all             ::1/128                 trust';" ^
  "$c = $c -replace '(?m)^local\s+all\s+all\s+\S+','local   all             all                                     trust';" ^
  "Set-Content '%PGDATA%\pg_hba.conf' $c"

net start %SERVICE%
timeout /t 3 /nobreak >nul

"%PSQL%" -h 127.0.0.1 -U postgres -d postgres -c "ALTER USER postgres WITH PASSWORD '%NEWPASS%';"
"%PSQL%" -h 127.0.0.1 -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='%DBNAME%'" | findstr /C:"1" >nul
if errorlevel 1 (
  "%PSQL%" -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE \"%DBNAME%\";"
)

copy /Y "%PGDATA%\pg_hba.conf.bak" "%PGDATA%\pg_hba.conf" >nul
net stop %SERVICE%
net start %SERVICE%

echo [OK] PostgreSQL user password set to: %NEWPASS%
echo [OK] Database ready: %DBNAME%
echo.
exit /b 0
