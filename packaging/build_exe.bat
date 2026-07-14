@echo off
REM One-click Windows build of xcaltool.exe (run from the repo root).
REM Uses 32-bit Python so the Nexiq RP1210 (NULN2R32.dll) can load at runtime.
setlocal

py -3-32 --version >nul 2>&1
if errorlevel 1 (
    echo 32-bit Python "py -3-32" not found. Install the Windows 32-bit
    echo Python from python.org, or edit this script to use your interpreter.
    exit /b 1
)

py -3-32 -m pip install --upgrade pyinstaller || exit /b 1
py -3-32 -m PyInstaller --noconfirm packaging\xcaltool.spec || exit /b 1

echo.
echo Done. Your app is at: dist\xcaltool.exe
endlocal
