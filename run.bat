@echo off
setlocal
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python315\python.exe" set "PYEXE="%LOCALAPPDATA%\Programs\Python\Python315\python.exe""
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PYEXE="%LOCALAPPDATA%\Programs\Python\Python314\python.exe""
if not defined PYEXE where python >nul 2>nul
if not defined PYEXE if not errorlevel 1 set "PYEXE=python"

if not defined PYEXE (
    echo [X] Python 3 not found. Install Python 3 first.
    pause
    exit /b 1
)

echo [i] Debug mode - console stays visible, log also written to:
echo     %LOCALAPPDATA%\EveMumbleOverlay\overlay.log
echo [i] For daily use, double-click the .vbs launcher instead.
echo.
%PYEXE% EveMumbleOverlay.pyw %*
echo.
echo [i] exited with code %ERRORLEVEL%
pause
