@echo off
REM Build the OUSSAMA Cutter single-file executable on Windows (Roadmap 1.1).
cd /d "%~dp0\.."
echo [1/4] Installing build deps...
pip install --quiet pyinstaller pyacoustid
echo [2/4] Building (onefile, console)...
pyinstaller packaging\viralcutter.spec --noconfirm --clean
echo [3/4] Done -^> dist\OUSSAMA-Cutter.exe
echo [4/4] Optional: download fpcalc.exe from
echo        https://github.com/acoustid/chromaprint/releases
echo        and put it next to the exe (enables the music fingerprint check).
dir dist
