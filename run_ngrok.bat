@echo off

setlocal EnableDelayedExpansion

cd /d "%~dp0"



echo ========================================

echo   GeoTrip Planner - ngrok Public URL

echo ========================================

echo.



set "APP_PORT=5000"

if exist "backend\.env" (

  for /f "usebackq tokens=1,* delims==" %%a in ("backend\.env") do (

    if /i "%%a"=="PORT" set "APP_PORT=%%b"

  )

)



if not defined NGROK_AUTHTOKEN (

  if exist "backend\.env" (

    for /f "usebackq tokens=1,* delims==" %%a in ("backend\.env") do (

      if /i "%%a"=="NGROK_AUTHTOKEN" set "NGROK_AUTHTOKEN=%%b"

    )

  )

)



if not defined NGROK_AUTHTOKEN (

  echo ERROR: NGROK_AUTHTOKEN is not set.

  echo.

  echo 1. Sign up free: https://dashboard.ngrok.com/signup

  echo 2. Copy your authtoken: https://dashboard.ngrok.com/get-started/your-authtoken

  echo 3. Run once:

  echo    ngrok config add-authtoken YOUR_TOKEN_HERE

  echo.

  echo Or add NGROK_AUTHTOKEN=your_token to backend\.env

  pause

  exit /b 1

)



if not exist ".venv\Scripts\python.exe" (

  echo ERROR: Virtual environment not found. Run run_server.bat first.

  pause

  exit /b 1

)



echo Starting server on port !APP_PORT! and ngrok tunnel...

echo.

start "GeoTrip Server" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe app.py"

timeout /t 3 /nobreak >nul

ngrok http !APP_PORT!

pause

