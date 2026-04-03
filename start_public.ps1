$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "py 명령을 찾을 수 없습니다."
}

if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY 환경 변수가 설정되어 있지 않습니다."
}

$port = if ($env:PORT) { $env:PORT } else { "5000" }
$ngrokPath = Join-Path $projectRoot "ngrok.exe"

if (-not (Test-Path $ngrokPath)) {
    throw "ngrok.exe 파일을 찾을 수 없습니다."
}

if ($env:NGROK_AUTHTOKEN) {
    & $ngrokPath config add-authtoken $env:NGROK_AUTHTOKEN | Out-Null
}

$pythonArgs = @(
    "-3",
    "-c",
    "from app import app; app.run(host='0.0.0.0', port=$port, debug=False)"
)

$appProcess = Start-Process -FilePath "py" -ArgumentList $pythonArgs -WorkingDirectory $projectRoot -PassThru
Start-Sleep -Seconds 3

if ($appProcess.HasExited) {
    throw "Flask 서버가 정상적으로 시작되지 않았습니다."
}

Write-Host "Flask 서버 PID: $($appProcess.Id)"
Write-Host "로컬 주소: http://127.0.0.1:$port"
Write-Host "잠시 후 ngrok가 외부 공개 URL을 표시합니다."

& $ngrokPath http $port
