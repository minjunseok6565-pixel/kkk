@echo off
setlocal

cd /d "%~dp0"

if not exist "saves" mkdir "saves"

set "LEAGUE_DB_PATH=%CD%\saves\_active_runtime.sqlite3"
set "PYTHONUTF8=1"

echo [NBA SIM] Starting server...
echo LEAGUE_DB_PATH=%LEAGUE_DB_PATH%

echo [NBA SIM] Opening browser at http://127.0.0.1:8000/static/NBA.html
start "" http://127.0.0.1:8000/static/NBA.html

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

if errorlevel 1 (
  echo.
  echo [ERROR] Failed to start server.
  echo - Make sure Python dependencies are installed.
  echo - Try: pip install fastapi uvicorn pandas openpyxl
  pause
)

endlocal
