@echo off
setlocal
title OUSSAMA Cutter

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [!] Virtual environment not found.
    echo     Run install_dependencies.bat once first, then run.bat again.
    pause
    exit /b 1
)

set "PYTHON=%CD%\.venv\Scripts\python.exe"

:: FFmpeg installed next to the app by install_dependencies.bat / packaging\install_ffmpeg_windows.bat
if exist "bin\ffmpeg.exe" set "PATH=%CD%\bin;%PATH%"

:: Pre-flight: check EVERYTHING and auto-install anything missing, so the
:: app starts with everything in place. --auto-fix installs missing core
:: dependencies automatically; --off skips the check.
echo.
echo [preflight] Checking environment and installing anything missing...
"%PYTHON%" -m scripts.preflight --auto-fix
set "PREFLIGHT_EXIT=%errorlevel%"
if "%PREFLIGHT_EXIT%"=="0" goto START_APP
if "%PREFLIGHT_EXIT%"=="2" goto START_APP_WARNINGS

echo.
echo [!] OUSSAMA Cutter وجد مشكلة حرجة في البيئة. أصلح العناصر أعلاه ثم أعد التشغيل.
echo     إصلاح التفريغ: setup_on_d.ps1 -Mode Full -Transcription cpu
echo     NVIDIA: setup_on_d.ps1 -Mode Full -Transcription gpu
echo     (أو set VIRALCUTTER_SKIP_PREFLIGHT=1 للتشغيل القسري للاختبار فقط)
if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1

:START_APP_WARNINGS
echo [preflight] تحذيرات اختيارية فقط؛ سيتم تشغيل OUSSAMA Cutter.

:START_APP
"%PYTHON%" main_improved.py %*
if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b %errorlevel%
pause
