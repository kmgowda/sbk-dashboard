$ErrorActionPreference = 'Stop'
$ScriptDirectory = $PSScriptRoot
$Selected = @($args)
$Mode = if ($Selected.Count -gt 0) { $Selected[0] } else { 'foreground' }
$DashboardArguments = if ($Selected.Count -gt 1) { @($Selected[1..($Selected.Count - 1)]) } else { @() }
$PythonArguments = @()

if ($Mode -notin @('foreground', 'background', 'stop', 'repair')) {
    Write-Error "Unknown SBK Dashboard launcher mode: $Mode" -ErrorAction Continue
    exit 2
}

if ($env:VIRTUAL_ENV) {
    $Python = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
    $EnvironmentDescription = "active virtual environment $env:VIRTUAL_ENV"
} elseif ($env:CONDA_PREFIX) {
    $Python = Join-Path $env:CONDA_PREFIX 'python.exe'
    $EnvironmentDescription = "active Conda environment $env:CONDA_PREFIX"
} else {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $PythonCommand) {
        Write-Error 'Python 3 is required, but no py or python command was found. Install Python with venv support and retry.' -ErrorAction Continue
        exit 1
    }
    $Python = $PythonCommand.Source
    if ($PythonCommand.Name -like 'py*') {
        $PythonArguments = @('-3')
    }
    $EnvironmentDescription = 'Python on PATH'
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "The selected $EnvironmentDescription has no Python executable at $Python. Reactivate a valid environment, or install a supported Python with venv support." -ErrorAction Continue
    exit 1
}

& $Python @PythonArguments (Join-Path $ScriptDirectory 'python_requirement.py')
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($Mode -ne 'stop') {
    Write-Host "Selected $EnvironmentDescription"
}
& $Python @PythonArguments (Join-Path $ScriptDirectory 'sbk_dashboard_bootstrap.py') $Mode @DashboardArguments
exit $LASTEXITCODE
