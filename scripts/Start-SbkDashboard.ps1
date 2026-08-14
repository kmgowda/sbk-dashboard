$ErrorActionPreference = 'Stop'
$ScriptDirectory = $PSScriptRoot
$ProjectDirectory = Split-Path -Parent $ScriptDirectory
$DashboardArguments = @($args)
$PythonArguments = @()

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
        $MissingPythonMessage = @'
Python 3.10 or newer is required, but no py or python command was found.
Install Python from https://www.python.org/downloads/windows/ and enable the Python launcher.
Install Python with its venv module, then rerun this script.
'@
        Write-Error $MissingPythonMessage -ErrorAction Continue
        exit 1
    }
    $Python = $PythonCommand.Source
    if ($PythonCommand.Name -like 'py*') {
        $PythonArguments = @('-3')
    }
    $EnvironmentDescription = 'Python on PATH'
}

if (-not (Test-Path -LiteralPath $Python)) {
    $BrokenEnvironmentMessage = @"
The selected $EnvironmentDescription has no Python executable at $Python.
Reactivate a valid environment, or recreate the project environment:
  py -3 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  python -m pip install .
"@
    Write-Error $BrokenEnvironmentMessage -ErrorAction Continue
    exit 1
}

& $Python @PythonArguments -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) {
    $PythonVersion = (& $Python @PythonArguments --version 2>&1) -join ' '
    Write-Error "Python 3.10 or newer is required; selected interpreter reports: $PythonVersion. Install a supported Python, then recreate or reactivate the venv/Conda environment." -ErrorAction Continue
    exit 1
}

Write-Host "Selected $EnvironmentDescription"
& $Python @PythonArguments (Join-Path $ScriptDirectory 'sbk_dashboard_bootstrap.py') foreground @DashboardArguments
exit $LASTEXITCODE
