@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  start "EbSynth Studio" pyw -3 gui\ebsynth_gui.py
) else (
  start "EbSynth Studio" pythonw gui\ebsynth_gui.py
)
