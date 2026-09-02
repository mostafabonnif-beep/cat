@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title OUSSAMA Cutter Windows Setup

echo ==========================================
echo OUSSAMA Cutter - Windows Setup
echo ==========================================
echo.

del /q ".installer_error.log" >nul 2>&1
if not exist ".installer-tmp" mkdir ".installer-tmp"
set "TEMP=%CD%\.installer-tmp"
set "TMP=%CD%\.installer-tmp"
set "UV_NO_CACHE=1"
set "UV_CACHE_DIR=%CD%\.uv-cache"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

rem Parse all optional arguments without interactive prompts or fragile cmd blocks.
set "GPU_MODE=cpu"
set "INSTALL_TRANSCRIBE=0"
set "INSTALL_FALLBACK=0"
set "INSTALL_UPLOAD=0"
set "UPLOAD_FAILED=0"
set "TRANSCRIPTION_FAILED=0"
set "TRANSCRIPTION_FALLBACK_FAILED=0"
:PARSE_ARGS
if "%~1"=="" goto ARGS_DONE
if /I "%~1"=="gpu" set "GPU_MODE=cuda"
if /I "%~1"=="full" set "INSTALL_TRANSCRIBE=1"
if /I "%~1"=="fallback" set "INSTALL_FALLBACK=1"
if /I "%~1"=="upload" set "INSTALL_UPLOAD=1"
shift
goto PARSE_ARGS
:ARGS_DONE

powershell -NoProfile -Command "$drive=(Get-Location).Path.Substring(0,1); $free=(Get-PSDrive -Name $drive).Free; Write-Host ('Free space on ' + $drive + ': ' + [math]::Round($free/1GB,2) + ' GB'); if ($free -lt 8GB) { exit 1 }"
if errorlevel 1 goto DISK_FAILED

where uv >nul 2>&1
if not errorlevel 1 goto UV_READY
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if not errorlevel 1 goto UV_READY

echo [1/6] Installing uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if not errorlevel 1 goto UV_READY

echo [ERROR] uv was not found after installation.
goto FAILED

:UV_READY
echo [1/6] uv is ready.

echo.
echo [2/6] Preparing Python 3.12 virtual environment...
if not exist ".venv\Scripts\python.exe" goto CREATE_VENV
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if (3,9) <= sys.version_info[:2] <= (3,12) else 1)"
if not errorlevel 1 goto VENV_READY
rmdir /s /q ".venv"
:CREATE_VENV
uv venv --python 3.12
if errorlevel 1 goto VENV_FAILED
:VENV_READY
if not exist ".venv\Scripts\python.exe" goto VENV_FAILED
set "PYTHON=.venv\Scripts\python.exe"
"%PYTHON%" --version

if "%INSTALL_TRANSCRIBE%"=="1" goto INSTALL_TRANSCRIPTION_RUNTIME
goto INSTALL_CORE

:INSTALL_TRANSCRIPTION_RUNTIME
echo.
echo [3/6] Installing OUSSAMA Cutter transcription runtime: %GPU_MODE%
if /I "%GPU_MODE%"=="cuda" goto INSTALL_CUDA
uv pip install --python "%PYTHON%" --no-cache torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto TRANSCRIPTION_TORCH_FAILED
goto INSTALL_CORE

:TRANSCRIPTION_TORCH_FAILED
set "TRANSCRIPTION_FAILED=1"
echo [ERROR] CPU PyTorch installation failed. Full transcription will not be ready.
goto INSTALL_CORE

:INSTALL_CUDA
echo Installing NVIDIA CUDA PyTorch runtime...
uv pip install --python "%PYTHON%" --no-cache torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 goto TRANSCRIPTION_TORCH_FAILED

goto INSTALL_CORE

:INSTALL_CORE
echo.
echo [3/6] Installing core WebUI dependencies...
uv pip install --python "%PYTHON%" --no-cache -r requirements.txt
if errorlevel 1 goto CORE_FAILED
uv pip install --python "%PYTHON%" --no-cache --upgrade "yt-dlp[default]"
if errorlevel 1 echo [WARNING] yt-dlp[default] installation failed; YouTube may need manual EJS setup.
where deno >nul 2>&1
if not errorlevel 1 echo Deno JavaScript runtime detected for YouTube.
if errorlevel 1 echo [INFO] Deno not found. Install Deno 2.3+ or use yt-dlp with another supported JS runtime if YouTube shows HTTP 403.

if "%INSTALL_TRANSCRIBE%"=="1" goto INSTALL_WHISPERX
if "%INSTALL_FALLBACK%"=="1" goto INSTALL_FASTER_FALLBACK
goto CHECK_FFMPEG

:INSTALL_WHISPERX
echo.
echo [4/6] Installing optional WhisperX stack...
uv pip install --python "%PYTHON%" --no-cache whisperx
if errorlevel 1 set "TRANSCRIPTION_FAILED=1"
uv pip install --python "%PYTHON%" --no-cache "huggingface-hub>=0.34.0,<1.0"
if errorlevel 1 set "TRANSCRIPTION_FAILED=1"
rem tokenizers 0.23.1 breaks the transformers import check because
rem transformers caps at 0.23.0 and 0.23.0 was never released on PyPI.
rem Pin below 0.23.1 to keep the known-good 0.22.2.
uv pip install --python "%PYTHON%" --no-cache "tokenizers>=0.22.0,<0.23.1"
if errorlevel 1 set "TRANSCRIPTION_FAILED=1"
uv pip install --python "%PYTHON%" --no-cache "numpy<2"
if errorlevel 1 set "TRANSCRIPTION_FAILED=1"
if /I "%GPU_MODE%"=="cuda" goto RESTORE_CUDA_TORCH
goto VERIFY_TRANSCRIPTION

:RESTORE_CUDA_TORCH
echo Restoring NVIDIA CUDA Torch after WhisperX dependency resolution...
uv pip install --python "%PYTHON%" --no-cache --reinstall torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 goto TRANSCRIPTION_TORCH_FAILED

:VERIFY_TRANSCRIPTION
"%PYTHON%" -c "import torch, torchaudio, whisperx; raise SystemExit(0 if (torch.version.cuda and torch.cuda.is_available()) else 1)" >nul 2>&1
if errorlevel 1 if /I "%GPU_MODE%"=="cuda" goto GPU_VERIFY_FAILED
"%PYTHON%" -c "import torch, torchaudio, whisperx" >nul 2>&1
if errorlevel 1 set "TRANSCRIPTION_FAILED=1"
if "%INSTALL_FALLBACK%"=="1" goto INSTALL_FASTER_FALLBACK
goto CHECK_FFMPEG

:INSTALL_FASTER_FALLBACK
echo.
echo [optional] Installing faster-whisper fallback...
uv pip install --python "%PYTHON%" --no-cache -r requirements-transcribe-fallback.txt
if errorlevel 1 set "TRANSCRIPTION_FALLBACK_FAILED=1"
if "%TRANSCRIPTION_FALLBACK_FAILED%"=="1" goto CHECK_FFMPEG
"%PYTHON%" -c "import faster_whisper" >nul 2>&1
if errorlevel 1 set "TRANSCRIPTION_FALLBACK_FAILED=1"
goto CHECK_FFMPEG

:GPU_VERIFY_FAILED
set "TRANSCRIPTION_FAILED=1"
echo [ERROR] CUDA Torch is installed but RTX/CUDA is not available to Python.
echo [INFO] Check the NVIDIA driver, then run the diagnostics command.
if "%INSTALL_FALLBACK%"=="1" goto INSTALL_FASTER_FALLBACK
goto CHECK_FFMPEG

:CHECK_FFMPEG
echo.
echo [5/6] Checking FFmpeg...
where ffmpeg >nul 2>&1
if not errorlevel 1 goto FFMPEG_READY
if exist "bin\ffmpeg.exe" goto FFMPEG_READY
if "%VIRALCUTTER_SKIP_FFMPEG%"=="1" goto FFMPEG_SKIPPED
if not exist "packaging\install_ffmpeg_windows.bat" goto FFMPEG_SKIPPED
call packaging\install_ffmpeg_windows.bat
if errorlevel 1 goto FFMPEG_SKIPPED
goto FFMPEG_READY

:FFMPEG_READY
echo FFmpeg is available.
goto OPTIONAL_UPLOAD
:FFMPEG_SKIPPED
echo [WARNING] FFmpeg was not found. Install it and add it to PATH before video processing.

goto OPTIONAL_UPLOAD

:OPTIONAL_UPLOAD
if "%INSTALL_UPLOAD%"=="1" goto INSTALL_UPLOAD
if "%VIRALCUTTER_UPLOAD%"=="1" goto INSTALL_UPLOAD
goto VERIFY
:INSTALL_UPLOAD
echo Installing YouTube OAuth dependencies...
uv pip install --python "%PYTHON%" --no-cache -r requirements-upload.txt
if errorlevel 1 set "UPLOAD_FAILED=1"
if "%UPLOAD_FAILED%"=="1" echo [ERROR] YouTube OAuth dependencies failed to install.
if "%UPLOAD_FAILED%"=="0" "%PYTHON%" -c "import google_auth_oauthlib, googleapiclient, google.oauth2; print('YouTube OAuth runtime: READY')"
if errorlevel 1 set "UPLOAD_FAILED=1"

goto VERIFY

:VERIFY
echo.
echo [6/6] Verifying environment...
"%PYTHON%" -m scripts.preflight --check
if errorlevel 1 echo [WARNING] Preflight reported remaining problems.
"%PYTHON%" -m scripts.windows_diagnostics --root "%CD%" --output ".windows-diagnostics.json"
if errorlevel 1 echo [WARNING] Windows diagnostics found a critical runtime problem.

echo.
echo ==========================================
echo Setup finished.
echo ==========================================
if "%INSTALL_TRANSCRIBE%"=="1" if "%TRANSCRIPTION_FAILED%"=="1" goto TRANSCRIPTION_INSTALL_FAILED
if "%UPLOAD_FAILED%"=="1" goto UPLOAD_INSTALL_FAILED

echo OUSSAMA Cutter Lightweight WebUI mode is ready.
echo Start with: run_webui.bat or run_webui_on_d.ps1
echo Full transcription: install_dependencies.bat full (CPU)
echo NVIDIA transcription: install_dependencies.bat gpu full
echo Optional transcription recovery: install_dependencies.bat fallback (or gpu fallback)
echo YouTube OAuth: install_dependencies.bat upload
if "%VIRALCUTTER_NO_PAUSE%"=="1" goto CLEAN_DONE
pause
:CLEAN_DONE
rmdir /s /q ".installer-tmp" >nul 2>&1
exit /b 0

:UPLOAD_INSTALL_FAILED
echo.
echo [ERROR] YouTube OAuth installation did not complete.
echo [INFO] Run: .\.venv\Scripts\python.exe -m pip install -r requirements-upload.txt
rmdir /s /q ".installer-tmp" >nul 2>&1
if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1

:TRANSCRIPTION_INSTALL_FAILED
echo.
echo [ERROR] Full transcription installation did not complete.
echo [INFO] Check the error above, free space on the project drive, and network access.
echo [INFO] Run diagnostics with: .\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --json
rmdir /s /q ".installer-tmp" >nul 2>&1
if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1

:DISK_FAILED
echo [ERROR] At least 8 GB free space is required on the project drive.
echo Delete temporary files or move D:\SS to a drive with more space.
goto FAILED
:VENV_FAILED
echo [ERROR] Could not create a Python 3.12 virtual environment.
goto FAILED
:CORE_FAILED
echo [ERROR] Core dependencies failed. Check free space, network, and Python 3.12.
goto FAILED
:FAILED
rmdir /s /q ".installer-tmp" >nul 2>&1
if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
