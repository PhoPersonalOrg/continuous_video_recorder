@echo off
cd /d "%~dp0.."
uv run epocx-eyecam-usb %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo The script exited with an error. Check the output above for details.
    pause
)
