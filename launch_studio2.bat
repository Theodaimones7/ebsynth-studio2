@echo off
setlocal
cd /d "%~dp0"
if exist "dist\EbSynthStudio2\EbSynthStudio2.exe" (
  start "EbSynth Studio 2" "dist\EbSynthStudio2\EbSynthStudio2.exe"
) else (
  python gui\studio_qt.py
)
