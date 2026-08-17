# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

$ErrorActionPreference = 'Stop'
$ScriptDirectory = $PSScriptRoot
$Selected = @($args)
$Mode = if ($Selected.Count -gt 0) { $Selected[0] } else { 'foreground' }
$DashboardArguments = if ($Selected.Count -gt 1) { @($Selected[1..($Selected.Count - 1)]) } else { @() }
$PythonArguments = @()
$Python = $null
$EnvironmentDescription = $null

if ($Mode -notin @('foreground', 'background', 'stop', 'repair')) {
    Write-Error "Unknown SBK Dashboard launcher mode: $Mode" -ErrorAction Continue
    exit 2
}

if ($env:VIRTUAL_ENV -and (Test-Path -LiteralPath (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'))) {
    $Python = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
    $EnvironmentDescription = "active virtual environment $env:VIRTUAL_ENV"
} elseif ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX 'python.exe'))) {
    $Python = Join-Path $env:CONDA_PREFIX 'python.exe'
    $EnvironmentDescription = "active Conda environment $env:CONDA_PREFIX"
} else {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($PythonCommand) {
        $Python = $PythonCommand.Source
        if ($PythonCommand.Name -like 'py*') {
            $PythonArguments = @('-3')
        }
        $EnvironmentDescription = 'Python on PATH'
    }
}

if ($Python -and (Test-Path -LiteralPath $Python)) {
    & $Python @PythonArguments (Join-Path $ScriptDirectory 'python_requirement.py') *> $null
    if ($LASTEXITCODE -eq 0) {
        if ($Mode -ne 'stop') { Write-Host "Selected $EnvironmentDescription" }
        & $Python @PythonArguments (Join-Path $ScriptDirectory 'sbk_dashboard_bootstrap.py') $Mode @DashboardArguments
        exit $LASTEXITCODE
    }
    & $Python @PythonArguments (Join-Path $ScriptDirectory 'python_requirement.py')
    Write-Warning 'Switching to the verified standalone runtime.'
} else {
    Write-Warning 'Python 3.10+ was not found; switching to the verified standalone runtime.'
}
& (Join-Path $ScriptDirectory 'Install-SbkDashboardPortable.ps1') $Mode @DashboardArguments
exit $LASTEXITCODE
