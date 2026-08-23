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
if not exist "lib" mkdir "lib"

echo [1/3] Building EbSynth CPU engine...
cl src\ebsynth.cpp src\ebsynth_cpu.cpp src\ebsynth_nocuda.cpp /DNDEBUG /O2 /openmp /EHsc /nologo /I"include" /Fe"bin\ebsynth.exe"
if errorlevel 1 exit /b 1

del ebsynth.obj ebsynth_cpu.obj ebsynth_nocuda.obj 2>nul

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo ERROR: PyInstaller is missing. Run: python -m pip install pyinstaller
  exit /b 1
)

echo [2/3] Building EbSynth Studio GUI...
python -m PyInstaller --noconfirm --clean --onedir --windowed --name EbSynthStudio gui\ebsynth_gui.py
if errorlevel 1 exit /b 1

echo [3/3] Adding engine and documentation...
if not exist "dist\EbSynthStudio\bin" mkdir "dist\EbSynthStudio\bin"
copy /y "bin\ebsynth.exe" "dist\EbSynthStudio\bin\ebsynth.exe" >nul
copy /y "GUI_README_RU.md" "dist\EbSynthStudio\README_RU.md" >nul

echo.
echo BUILD COMPLETE:
echo %CD%\dist\EbSynthStudio\EbSynthStudio.exe
