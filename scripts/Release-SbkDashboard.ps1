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
$Python = $null
$PythonArguments = @()

if ($env:VIRTUAL_ENV -and (Test-Path -LiteralPath (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'))) {
    $Python = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
} elseif ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX 'python.exe'))) {
    $Python = Join-Path $env:CONDA_PREFIX 'python.exe'
} else {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { $PythonCommand = Get-Command python -ErrorAction SilentlyContinue }
    if ($PythonCommand) {
        $Python = $PythonCommand.Source
        if ($PythonCommand.Name -in @('py', 'py.exe')) { $PythonArguments = @('-3') }
    }
}

if (-not $Python) {
    Write-Error 'SBK Dashboard release engineering requires Python 3.10+ and Git in a source checkout.' -ErrorAction Continue
    exit 1
}

& $Python @PythonArguments (Join-Path $ScriptDirectory 'python_requirement.py') *> $null
if ($LASTEXITCODE -ne 0) {
    & $Python @PythonArguments (Join-Path $ScriptDirectory 'python_requirement.py')
    exit $LASTEXITCODE
}

& $Python @PythonArguments (Join-Path $ScriptDirectory 'release.py') @args
exit $LASTEXITCODE
