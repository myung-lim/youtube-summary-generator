$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "py launcher not found"
}

if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not set"
}

# Clear proxy env vars that can break YouTube requests
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""

$port = if ($env:PORT) { $env:PORT } else { "5000" }
$ngrokPath = Join-Path $projectRoot "ngrok.exe"

if (-not (Test-Path $ngrokPath)) {
    throw "ngrok.exe not found"
}

if ($env:NGROK_AUTHTOKEN) {
    & $ngrokPath config add-authtoken $env:NGROK_AUTHTOKEN | Out-Null
}

# Start Flask in background
$pythonArgs = "-3 -c ""from app import app; app.run(host='0.0.0.0', port=$port, debug=False)"""

$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$logPath = Join-Path $logDir ("flask_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

$errPath = Join-Path $logDir ("flask_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".err.log")
$appProcess = Start-Process -FilePath "py" -ArgumentList $pythonArgs -WorkingDirectory $projectRoot -PassThru -RedirectStandardOutput $logPath -RedirectStandardError $errPath

# Wait up to 15s for the server to bind
$started = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    $listening = netstat -ano | Select-String -Pattern ":$port\s+.*LISTENING"
    if ($listening) {
        $started = $true
        break
    }
    if ($appProcess.HasExited) { break }
}

if (-not $started) {
    if (Test-Path $logPath) {
        Write-Host "Flask log: $logPath"
        Get-Content $logPath -Tail 50 | ForEach-Object { Write-Host $_ }
    }
    throw "Flask server failed to start"
}

# Start ngrok in background
$ngrokProcess = Start-Process -FilePath $ngrokPath -ArgumentList "http $port" -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden

# Wait for ngrok API
$publicUrl = $null
for ($i = 0; $i -lt 20; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri http://127.0.0.1:4040/api/tunnels
        if ($resp.tunnels -and $resp.tunnels.Count -gt 0) {
            $publicUrl = $resp.tunnels[0].public_url
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
    Start-Sleep -Milliseconds 500
}

Write-Host "Flask PID: $($appProcess.Id)"
Write-Host "ngrok PID: $($ngrokProcess.Id)"
Write-Host "Local URL: http://127.0.0.1:$port"
if ($publicUrl) {
    Write-Host "Public URL: $publicUrl"
    if (Get-Command Set-Clipboard -ErrorAction SilentlyContinue) {
        Set-Clipboard -Value $publicUrl
        Write-Host "Public URL copied to clipboard."
    }
    Start-Process $publicUrl
} else {
    Write-Host "Public URL not available. If ngrok requires auth, set NGROK_AUTHTOKEN and rerun."
}
