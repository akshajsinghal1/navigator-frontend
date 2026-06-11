# Start Navigator demo — API + frontend
# Usage: .\scripts\start_demo.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py = "C:\Users\aksha\AppData\Local\Programs\Python\Python312\python.exe"

Write-Host "Starting API on http://127.0.0.1:8002 ..."
Start-Process -FilePath $Py -ArgumentList "-m", "uvicorn", "api.main:app", "--port", "8002", "--host", "127.0.0.1" `
  -WorkingDirectory $Root -WindowStyle Normal

Start-Sleep -Seconds 3

Write-Host "Starting Vite frontend..."
Start-Process -FilePath "npm" -ArgumentList "run", "dev" `
  -WorkingDirectory (Join-Path $Root "frontend") -WindowStyle Normal

Write-Host ""
Write-Host "Demo URL (check Vite terminal for exact port):"
Write-Host "  http://localhost:5173/?workbook=NAVIGATOR_DEMO"
Write-Host ""
Write-Host "Test API: http://127.0.0.1:8002/health"
