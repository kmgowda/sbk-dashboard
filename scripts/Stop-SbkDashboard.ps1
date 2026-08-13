$ErrorActionPreference = 'Stop'
$ScriptDirectory = $PSScriptRoot
$ProjectDirectory = Split-Path -Parent $ScriptDirectory

if ($env:VIRTUAL_ENV) {
    $Python = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
    $EnvironmentDescription = "active virtual environment $env:VIRTUAL_ENV"
} elseif ($env:CONDA_PREFIX) {
    $Python = Join-Path $env:CONDA_PREFIX 'python.exe'
    $EnvironmentDescription = "active Conda environment $env:CONDA_PREFIX"
} elseif (Test-Path -LiteralPath (Join-Path $ProjectDirectory '.venv\Scripts\python.exe')) {
    $Python = Join-Path $ProjectDirectory '.venv\Scripts\python.exe'
    $EnvironmentDescription = "project virtual environment $(Join-Path $ProjectDirectory '.venv')"
} else {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $PythonCommand) {
        throw 'No Python executable was found to run the stop helper.'
    }
    $Python = $PythonCommand.Source
    $EnvironmentDescription = 'Python on PATH'
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "The selected $EnvironmentDescription has no Python executable at $Python. Reactivate the environment used to start SBK Dashboard." -ErrorAction Continue
    exit 1
}

& $Python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) {
    $PythonVersion = (& $Python --version 2>&1) -join ' '
    Write-Error "Python 3.10 or newer is required; selected interpreter reports: $PythonVersion. Reactivate the supported environment used to start SBK Dashboard." -ErrorAction Continue
    exit 1
}

& $Python (Join-Path $ScriptDirectory 'sbk_dashboard_launcher.py') stop
exit $LASTEXITCODE
