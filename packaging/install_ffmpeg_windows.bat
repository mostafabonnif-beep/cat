@echo off
REM Downloads a static ffmpeg build next to the executable (Roadmap 1.1).
REM Usage: scripts\install_ffmpeg_windows.bat   (run once after install)
set "DEST=%~dp0..\bin"
if not exist "%DEST%" mkdir "%DEST%"
echo Downloading ffmpeg essentials (gyan.dev static build)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%TEMP%\ffmpeg.zip'; Expand-Archive '%TEMP%\ffmpeg.zip' '%TEMP%\ffmpeg_x' -Force; $bin=Get-ChildItem '%TEMP%\ffmpeg_x' -Recurse -Filter ffmpeg.exe | Select-Object -First 1; Copy-Item $bin.FullName '%DEST%\ffmpeg.exe'; $p=Get-ChildItem '%TEMP%\ffmpeg_x' -Recurse -Filter ffprobe.exe | Select-Object -First 1; Copy-Item $p.FullName '%DEST%\ffprobe.exe'; Remove-Item '%TEMP%\ffmpeg.zip','%TEMP%\ffmpeg_x' -Recurse -Force"
echo Done. ffmpeg installed in %DEST%
