@echo off
REM Build citect-tracker.exe for Windows
REM Requirements: Python 3.10+ must be installed and on PATH

echo === Citect Record Tracker - Windows Build ===
echo.

REM Create venv if it doesn't exist
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate and install
echo Installing dependencies...
call .venv\Scripts\activate.bat
pip install -e .[build] --quiet

REM Build
echo.
echo Building executable...
pyinstaller citect-tracker.spec --noconfirm

echo.
if exist "dist\citect-tracker.exe" (
    echo Build successful!
    echo Output: dist\citect-tracker.exe
    for %%A in ("dist\citect-tracker.exe") do echo Size: %%~zA bytes
) else (
    echo Build FAILED. Check output above for errors.
    exit /b 1
)
