@echo off
setlocal EnableExtensions

set "PRODUCT_NAME=Poliora"
set "PACKAGE_NAME=poliora"
set "PACKAGE_SOURCE=%POLIORA_PACKAGE_SOURCE%"
set "INSTALL_ROOT=%LOCALAPPDATA%\Poliora"
set "VENV=%INSTALL_ROOT%\runtime"

title %PRODUCT_NAME% local workspace setup
echo.
echo ================================================================
echo   %PRODUCT_NAME% local workspace setup
echo ================================================================
echo.
echo This installs a private Python environment in:
echo   %VENV%
echo.
echo %PRODUCT_NAME% does not upload prompts, source code, or usage data.
echo.

set "PYTHON_COMMAND="
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON_COMMAND=python"
for %%V in (3.14 3.13 3.12 3.11) do (
  if not defined PYTHON_COMMAND py -%%V -c "import sys" >nul 2>&1 && set "PYTHON_COMMAND=py -%%V"
)
if not defined PYTHON_COMMAND (
  echo Python 3.11 or later is required before setup can continue.
  echo Install it from https://www.python.org/downloads/ then run this file again.
  echo.
  pause
  exit /b 1
)

if "%PACKAGE_SOURCE%"=="" set "PACKAGE_SOURCE=%PACKAGE_NAME%"

echo [1/4] Creating the private runtime...
%PYTHON_COMMAND% -m venv "%VENV%"
if errorlevel 1 goto :failed

echo [2/4] Updating the installer tools...
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
if errorlevel 1 goto :failed

echo [3/4] Installing %PRODUCT_NAME%...
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade "%PACKAGE_SOURCE%"
if errorlevel 1 (
  echo.
  echo %PRODUCT_NAME% could not be downloaded from the public package registry.
  echo Check your internet connection or try again after the launch announcement.
  goto :failed
)

echo [4/4] Checking supported local AI tools...
"%VENV%\Scripts\python.exe" -m poliora.main scan
if errorlevel 1 goto :failed

echo.
echo ================================================================
echo   %PRODUCT_NAME% setup finished.
echo ================================================================
echo.
echo Starting the local Poliora dashboard now...
if "%POLIORA_NO_LAUNCH%"=="1" exit /b 0
start "Poliora dashboard" cmd /k ""%VENV%\Scripts\python.exe" -m poliora.app_launcher"
echo.
timeout /t 2 /nobreak >nul
exit /b 0

:failed
echo.
echo Setup did not complete. Check error messages above.
echo.
pause
exit /b 1
