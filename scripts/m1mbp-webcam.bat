@echo off
cd /d "%~dp0.."
uv run m1mbp-webcam %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo The script exited with an error. Check the output above for details.
    pause
)
