param([switch]$NoBrowser, [switch]$Browser, [switch]$AppOnly)
$ErrorActionPreference = 'Stop'
$papiRoot = $PSScriptRoot
$papiUrl = 'http://127.0.0.1:8000'
function Open-PapiWindow {
    if ($Browser -and -not $AppOnly) { Start-Process "$papiUrl/dashboard"; return }
    $papiCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    $papiAppBrowser = $papiCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $papiAppBrowser) {
        if ($AppOnly) { throw 'PAPI needs Microsoft Edge or Google Chrome to open its app window.' }
        Write-Warning 'Install Microsoft Edge or Google Chrome for a dedicated PAPI window. Opening the default browser instead.'
        Start-Process "$papiUrl/dashboard"
        return
    }
    # Use the existing browser profile so local sign-ins and preferences persist.
    Start-Process -FilePath $papiAppBrowser -ArgumentList @("--app=$papiUrl/dashboard", '--window-size=1280,900') -WindowStyle Normal | Out-Null
}
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
    if (-not $NoBrowser) { Open-PapiWindow }
    Write-Output 'PAPI is ready.'
} catch {
    if ($NoBrowser -or $AppOnly) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'PAPI', 'OK', 'Error') | Out-Null
    exit 1
}