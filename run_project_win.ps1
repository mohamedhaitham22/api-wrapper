param(
    [string]$VenvPath = ".venv",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8005,
    [int]$MaxPortRetries = 20,
    [switch]$NoReload,
    [switch]$ForceInstallRequirements
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptRoot

if (-not (Test-Path -Path $VenvPath)) {
    Write-Host "Creating virtual environment at '$VenvPath'..."
    python -m venv $VenvPath
}

$pythonCandidates = @(
    (Join-Path $scriptRoot "$VenvPath\Scripts\python.exe"),
    (Join-Path $scriptRoot "$VenvPath\bin\python.exe")
)

$venvPython = $pythonCandidates | Where-Object { Test-Path -Path $_ } | Select-Object -First 1

if (-not $venvPython) {
    throw "Venv Python not found. Checked: $($pythonCandidates -join ', ')"
}

if (-not (Test-Path -Path "requirements.txt")) {
    throw "requirements.txt was not found in $scriptRoot"
}

if (-not (Test-Path -Path ".env")) {
    if (Test-Path -Path ".env.example") {
        Copy-Item -Path ".env.example" -Destination ".env"
        Write-Host "Created .env from .env.example"
    }
    else {
        throw ".env and .env.example are both missing. Create .env before running."
    }
}

$envContent = Get-Content -Path ".env" -ErrorAction Stop

function Get-EnvValue {
    param([string]$Name)

    $pattern = "^$([Regex]::Escape($Name))=(.*)$"
    foreach ($line in $envContent) {
        if ($line -match $pattern) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

$apiKey = Get-EnvValue -Name "MOCKAROO_API_KEY"
$mockarooUrl = Get-EnvValue -Name "MOCKAROO_URL"

if ([string]::IsNullOrWhiteSpace($apiKey) -or [string]::IsNullOrWhiteSpace($mockarooUrl)) {
    throw "MOCKAROO_API_KEY and MOCKAROO_URL must be set in .env"
}

$depsMissing = $false
try {
    & $venvPython -m pip show fastapi uvicorn httpx python-dotenv pydantic cachetools 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        $depsMissing = $true
    }
}
catch {
    $depsMissing = $true
}

if ($depsMissing -or $ForceInstallRequirements) {
    Write-Host "Installing dependencies from requirements.txt..."
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Fix the pip error above and rerun the script."
    }
}

function Test-PortAvailable {
    param(
        [string]$BindAddress,
        [int]$PortToTest
    )

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse($BindAddress), $PortToTest)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

$selectedPort = $Port
$portFound = $false

for ($i = 0; $i -le $MaxPortRetries; $i++) {
    $candidatePort = $Port + $i
    if (Test-PortAvailable -BindAddress $BindHost -PortToTest $candidatePort) {
        $selectedPort = $candidatePort
        $portFound = $true
        break
    }
}

if (-not $portFound) {
    throw "No available port found from $Port to $($Port + $MaxPortRetries) on host $BindHost."
}

if ($selectedPort -ne $Port) {
    Write-Host "Port $Port is unavailable. Using port $selectedPort instead."
}

$uvicornArgs = @("-m", "uvicorn", "main:app", "--host", $BindHost, "--port", "$selectedPort")
if (-not $NoReload) {
    $uvicornArgs += "--reload"
}

Write-Host "Starting FastAPI app on ${BindHost}:${selectedPort}..."
& $venvPython @uvicornArgs
