$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Selected = @($args)
$Command = if ($Selected.Count -gt 0) { $Selected[0] } else { 'foreground' }
$Remaining = if ($Selected.Count -gt 1) { @($Selected[1..($Selected.Count - 1)]) } else { @() }

switch ($Command) {
    { $_ -in @('start', 'foreground') } { & (Join-Path $Root 'scripts\Start-SbkDashboard.ps1') @Remaining; exit $LASTEXITCODE }
    'background' { & (Join-Path $Root 'scripts\Start-SbkDashboardBackground.ps1') @Remaining; exit $LASTEXITCODE }
    'stop' { & (Join-Path $Root 'scripts\Stop-SbkDashboard.ps1') @Remaining; exit $LASTEXITCODE }
    'repair' {
        $PythonArguments = @()
        if ($env:VIRTUAL_ENV) { $PythonPath = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' }
        elseif ($env:CONDA_PREFIX) { $PythonPath = Join-Path $env:CONDA_PREFIX 'python.exe' }
        else {
            $Python = Get-Command py -ErrorAction SilentlyContinue
            if ($Python) { $PythonPath = $Python.Source; $PythonArguments = @('-3') }
            else { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
        }
        & $PythonPath @PythonArguments (Join-Path $Root 'scripts\sbk_dashboard_bootstrap.py') repair @Remaining
        exit $LASTEXITCODE
    }
    default { & (Join-Path $Root 'scripts\Start-SbkDashboard.ps1') @Selected; exit $LASTEXITCODE }
}
