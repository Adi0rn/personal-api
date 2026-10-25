param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$papiRoot = $PSScriptRoot
$papiUrl = 'http://127.0.0.1:8000'
function Test-PapiReady {
    try {
        $health = Invoke-RestMethod -Uri "$papiUrl/health" -TimeoutSec 2
        return ($health.status -eq 'ok')
    } catch { return $false }
}
try {
    if (-not (Test-PapiReady)) {
        $papiPython = Join-Path $papiRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $papiPython)) {
            throw 'PAPI needs its Python environment. Follow the setup instructions in README.md first.'
        }
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if (-not $listener) {
            Start-Process -FilePath $papiPython -ArgumentList @('-m','uvicorn','main:app','--host','127.0.0.1','--port','8000','--no-access-log') -WorkingDirectory $papiRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $papiRoot 'server.log') -RedirectStandardError (Join-Path $papiRoot 'server-error.log') | Out-Null
        }
        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if (Test-PapiReady) { $ready = $true; break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw 'PAPI could not start. Port 8000 may be in use. Check server-error.log in the PAPI folder.' }
    }
    if (-not $NoBrowser) { Start-Process "$papiUrl/dashboard" }
    Write-Output 'PAPI is ready.'
} catch {
    if ($NoBrowser) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'PAPI', 'OK', 'Error') | Out-Null
    exit 1
}