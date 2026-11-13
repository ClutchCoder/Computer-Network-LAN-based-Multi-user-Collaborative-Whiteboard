@echo off
echo ==============================================================
echo LAN Whiteboard - Packaging Server and Client with PyInstaller
echo ==============================================================
echo.

python -m pip show pyinstaller >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo PyInstaller not detected. Installing PyInstaller...
    pip install pyinstaller
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

python build.py --target all

echo.
echo Build process finished. Check the 'dist' directory.
pause
