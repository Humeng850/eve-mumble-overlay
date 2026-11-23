@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python315\python.exe"
if not exist "%PY%" set "PY=python"

echo [i] Building single-file exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name EveMumbleOverlay --icon "%cd%\overlay.ico" ^
  --distpath dist --workpath build --specpath build EveMumbleOverlay.pyw
if errorlevel 1 goto fail

copy /y "dist\EveMumbleOverlay.exe" "EveMumbleOverlay.exe" >nul
if errorlevel 1 goto locked

rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
echo [OK] EveMumbleOverlay.exe updated.
pause
exit /b 0

:locked
echo [X] Cannot overwrite EveMumbleOverlay.exe - it is still running.
echo     Right-click the tray icon and choose Exit, then run build.bat again.
echo     New build is kept in dist\EveMumbleOverlay.exe
pause
exit /b 1

:fail
echo [X] Build failed, see output above.
pause
exit /b 1
