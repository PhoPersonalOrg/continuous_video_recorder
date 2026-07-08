@echo off
cd /d "%~dp0.."
uv run debut-example %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo The script exited with an error. Check the output above for details.
    pause
)
