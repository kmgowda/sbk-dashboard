$ErrorActionPreference = 'Stop'
$ScriptDirectory = $PSScriptRoot
$ProjectDirectory = Split-Path -Parent $ScriptDirectory

if ($env:VIRTUAL_ENV) {
    $Python = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
} elseif ($env:CONDA_PREFIX) {
    $Python = Join-Path $env:CONDA_PREFIX 'python.exe'
} elseif (Test-Path -LiteralPath (Join-Path $ProjectDirectory '.venv\Scripts\python.exe')) {
    $Python = Join-Path $ProjectDirectory '.venv\Scripts\python.exe'
} else {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $PythonCommand) {
        throw 'No Python executable was found to run the stop helper.'
    }
    $Python = $PythonCommand.Source
}

& $Python (Join-Path $ScriptDirectory 'sbk_dashboard_launcher.py') stop
exit $LASTEXITCODE
