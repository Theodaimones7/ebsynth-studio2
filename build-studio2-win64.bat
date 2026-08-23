@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VS_DIR="
for /f "usebackq tokens=*" %%i in (`vswhere.exe -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS_DIR=%%i"
if not defined VS_DIR (
  echo ERROR: Visual Studio Build Tools with C++ were not found.
  exit /b 1
)

call "%VS_DIR%\VC\Auxiliary\Build\vcvarsall.bat" amd64
if errorlevel 1 exit /b 1
if not exist "bin" mkdir "bin"

echo [1/4] Building EbSynth CPU engine...
cl src\ebsynth.cpp src\ebsynth_cpu.cpp src\ebsynth_nocuda.cpp /DNDEBUG /O2 /openmp /EHsc /nologo /I"include" /Fe"bin\ebsynth.exe"
if errorlevel 1 exit /b 1
del ebsynth.obj ebsynth_cpu.obj ebsynth_nocuda.obj 2>nul

python -c "import PyInstaller, PySide6" 2>nul
if errorlevel 1 (
  echo ERROR: Run: python -m pip install pyinstaller PySide6
  exit /b 1
)

echo [2/4] Building the Qt editor...
python -m PyInstaller --noconfirm --clean --onedir --windowed --name EbSynthStudio2 --paths gui gui\studio_qt.py
if errorlevel 1 exit /b 1

echo [3/4] Adding the synthesis engine...
if not exist "dist\EbSynthStudio2\bin" mkdir "dist\EbSynthStudio2\bin"
copy /y "bin\ebsynth.exe" "dist\EbSynthStudio2\bin\ebsynth.exe" >nul

echo [4/4] Adding FFmpeg and documentation...
for /f "delims=" %%i in ('where ffmpeg.exe 2^>nul') do if not exist "dist\EbSynthStudio2\bin\ffmpeg.exe" copy /y "%%i" "dist\EbSynthStudio2\bin\ffmpeg.exe" >nul
for /f "delims=" %%i in ('where ffprobe.exe 2^>nul') do if not exist "dist\EbSynthStudio2\bin\ffprobe.exe" copy /y "%%i" "dist\EbSynthStudio2\bin\ffprobe.exe" >nul
if not exist "dist\EbSynthStudio2\bin\ffmpeg.exe" (
  echo ERROR: ffmpeg.exe was not found in PATH.
  exit /b 1
)
if not exist "dist\EbSynthStudio2\bin\ffprobe.exe" (
  echo ERROR: ffprobe.exe was not found in PATH.
  exit /b 1
)
copy /y "STUDIO2_README_RU.md" "dist\EbSynthStudio2\README_RU.md" >nul

echo.
echo BUILD COMPLETE:
echo %CD%\dist\EbSynthStudio2\EbSynthStudio2.exe
