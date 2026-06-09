@echo off
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo ERROR: Run as Administrator
  exit /b 1
)

set PGDATA=C:\Program Files\PostgreSQL\18\data
set PSQL=C:\Program Files\PostgreSQL\18\bin\psql.exe
set SERVICE=postgresql-x64-18
set NEWPASS=postgres

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
"%PSQL%" -h 127.0.0.1 -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='geotrip_planner'" | findstr /C:"1" >nul
if %errorlevel% neq 0 (
  "%PSQL%" -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE geotrip_planner;"
)

copy /Y "%PGDATA%\pg_hba.conf.bak" "%PGDATA%\pg_hba.conf" >nul
net stop %SERVICE%
net start %SERVICE%
echo OK
