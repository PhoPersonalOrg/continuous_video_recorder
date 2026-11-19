$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $scriptDir "..")
uv run debut-example $args

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "The script exited with an error. Check the output above for details." -ForegroundColor Red
    Write-Host "Press Enter to exit..."
    Read-Host
}
