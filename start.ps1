$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .venv --without-pip
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create virtual environment.' }
}
& $taskPython -c 'import fastapi, streamlit, langgraph, ortools, plotly, httpx, langgraph.checkpoint.sqlite' 2>$null
if ($LASTEXITCODE -ne 0) {
    python -m pip --python $taskPython install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$apiProcess = Start-Process -FilePath $taskPython -ArgumentList @('-m', 'uvicorn', 'manufacturing.api:app', '--host', '127.0.0.1', '--port', '8010') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput 'runtime-api.log' -RedirectStandardError 'runtime-api-error.log'
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($apiProcess.HasExited) { throw 'API exited. Check runtime-api-error.log; port 8010 may already be in use.' }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8010/health' -TimeoutSec 1
            if ($health.status -eq 'ok') { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'API did not become ready.' }
    & $taskPython -m streamlit run ui.py --server.port 8501 --server.address 127.0.0.1
} finally {
    if (-not $apiProcess.HasExited) { Stop-Process -Id $apiProcess.Id }
}
