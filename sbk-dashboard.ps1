# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

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
        & (Join-Path $Root 'scripts\Invoke-SbkDashboard.ps1') repair @Remaining
        exit $LASTEXITCODE
    }
    'release' { & (Join-Path $Root 'scripts\Release-SbkDashboard.ps1') @Remaining; exit $LASTEXITCODE }
    default { & (Join-Path $Root 'scripts\Start-SbkDashboard.ps1') @Selected; exit $LASTEXITCODE }
}
