@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Poliora's virtual environment was not found at:
  echo   %CD%\.venv
  echo.
  echo Run this once from this project folder:
  echo   py -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e .
  echo.
  pause
  exit /b 1
)

echo Starting Poliora dashboard on http://127.0.0.1:8787
start "Poliora Dashboard - Ctrl+C to stop" cmd /k ""%CD%\.venv\Scripts\python.exe" -m poliora.main dashboard --no-open --port 8787"

echo Starting public website preview on http://127.0.0.1:8796
start "Poliora Website - Ctrl+C to stop" cmd /k ""%CD%\.venv\Scripts\python.exe" -m http.server 8796 --bind 127.0.0.1 --directory "%CD%\site""

echo Waiting for the local servers...
timeout /t 3 /nobreak >nul

start "" "http://127.0.0.1:8787"
start "" "http://127.0.0.1:8796"

echo.
echo Poliora previews started.
echo.
echo Dashboard:     http://127.0.0.1:8787
echo Public website: http://127.0.0.1:8796
echo.
echo To stop them, press Ctrl+C in each titled Command Prompt window.
echo You can close this launcher window now.
endlocal
