$ErrorActionPreference = 'Stop'

$ScriptDirectory = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $ScriptDirectory
$BootstrapPropertiesPath = Join-Path $ScriptDirectory 'portable-bootstrap.properties'
$Selected = @($args)
$Mode = if ($Selected.Count -gt 0) { $Selected[0] } else { 'foreground' }
$DashboardArguments = if ($Selected.Count -gt 1) { @($Selected[1..($Selected.Count - 1)]) } else { @() }
$BootstrapProperties = @{}
foreach ($Line in Get-Content -LiteralPath $BootstrapPropertiesPath) {
    if ($Line -match '^([^=]+)=(.*)$') { $BootstrapProperties[$Matches[1]] = $Matches[2] }
}
$RepositoryUrl = $BootstrapProperties['repository.url']
$MaximumArchiveBytes = [long]$BootstrapProperties['archive.max.bytes']
$MaximumChecksumBytes = [long]$BootstrapProperties['checksum.max.bytes']
$LockWaitSeconds = [int]$BootstrapProperties['lock.wait.seconds']
$LockStaleSeconds = [int]$BootstrapProperties['lock.stale.seconds']
if (-not $RepositoryUrl -or $MaximumArchiveBytes -le 0 -or $MaximumChecksumBytes -le 0 -or
    $LockWaitSeconds -le 0 -or $LockStaleSeconds -le 0) {
    throw 'Portable bootstrap properties are invalid.'
}

$VersionLine = Get-Content -LiteralPath (Join-Path $ProjectRoot 'src\sbk_dashboard\version.py') |
    Where-Object { $_ -match '^VERSION = "([^"]+)"$' } |
    Select-Object -First 1
if (-not $VersionLine -or $VersionLine -notmatch '^VERSION = "([^"]+)"$') {
    throw 'Unable to determine the SBK Dashboard version.'
}
$Version = $Matches[1]

$Architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
switch ($Architecture) {
    'x64' { $ArchitectureId = 'amd64' }
    'arm64' { $ArchitectureId = 'arm64' }
    default { throw "No standalone SBK Dashboard runtime is available for architecture $Architecture." }
}
$PlatformId = "windows-$ArchitectureId"

$HomeValue = if ($env:SBK_DASHBOARD_HOME) { $env:SBK_DASHBOARD_HOME } else { Join-Path $HOME '.sbk-dashboard' }
$PortableHome = [System.IO.Path]::GetFullPath($HomeValue)
$UserHome = [System.IO.Path]::GetFullPath($HOME).TrimEnd('\')
if ($PortableHome.TrimEnd('\') -eq $UserHome -or $PortableHome -eq [System.IO.Path]::GetPathRoot($PortableHome)) {
    throw 'SBK_DASHBOARD_HOME must be a dedicated subdirectory.'
}

$ArchiveName = "sbk-dashboard-$Version-$PlatformId.zip"
$BaseUrl = if ($env:SBK_DASHBOARD_PORTABLE_BASE_URL) {
    $env:SBK_DASHBOARD_PORTABLE_BASE_URL.TrimEnd('/')
} else {
    "$RepositoryUrl/releases/download/v$Version"
}
$BaseUri = [System.Uri]$BaseUrl
if ($BaseUri.Scheme -notin @('https', 'file')) {
    throw 'Portable runtime URL must use HTTPS (or file:// for an offline mirror).'
}
$CacheDirectory = Join-Path $PortableHome 'cache\releases'
$InstallParent = Join-Path $PortableHome "distributions\$Version\$PlatformId"
$InstallDirectory = Join-Path $InstallParent $ArchiveName
$Executable = Join-Path $InstallDirectory "sbk-dashboard-$Version-$PlatformId\sbk-dashboard.exe"
$Marker = Join-Path $InstallDirectory '.installed-sha256'
$LockDirectory = Join-Path $PortableHome 'launcher\bootstrap-locks'
$LockPath = Join-Path $LockDirectory "$Version-$PlatformId.lock"

function Receive-BoundedFile {
    param([string]$Uri, [string]$Destination, [long]$MaximumBytes)
    $SelectedUri = [System.Uri]$Uri
    if ($SelectedUri.IsFile) {
        $Source = [System.IO.File]::OpenRead($SelectedUri.LocalPath)
        try {
            if ($Source.Length -gt $MaximumBytes) { throw "Download exceeds the $MaximumBytes byte limit." }
            $Output = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try { $Source.CopyTo($Output); $Output.Flush($true) } finally { $Output.Dispose() }
        } finally {
            $Source.Dispose()
        }
        return
    }
    Add-Type -AssemblyName System.Net.Http
    $LastError = $null
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        $Response = $null
        $Client = [System.Net.Http.HttpClient]::new()
        $Client.Timeout = [TimeSpan]::FromMinutes(10)
        try {
            $Response = $Client.GetAsync($Uri, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            $Response.EnsureSuccessStatusCode() | Out-Null
            if ($Response.Content.Headers.ContentLength -and $Response.Content.Headers.ContentLength -gt $MaximumBytes) {
                throw "Download exceeds the $MaximumBytes byte limit."
            }
            $InputStream = $Response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $OutputStream = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try {
                $Buffer = [byte[]]::new(65536)
                $Total = 0L
                while (($Count = $InputStream.Read($Buffer, 0, $Buffer.Length)) -gt 0) {
                    $Total += $Count
                    if ($Total -gt $MaximumBytes) { throw "Download exceeds the $MaximumBytes byte limit." }
                    $OutputStream.Write($Buffer, 0, $Count)
                }
                $OutputStream.Flush($true)
            } finally {
                $OutputStream.Dispose()
                $InputStream.Dispose()
            }
            return
        } catch {
            $LastError = $_
            Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            if ($Attempt -lt 3) { Start-Sleep -Seconds $Attempt }
        } finally {
            if ($Response) { $Response.Dispose() }
            $Client.Dispose()
        }
    }
    throw $LastError
}

function Test-PortableRuntime {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Marker -PathType Leaf)) { return $false }
    $MarkerLines = @(Get-Content -LiteralPath $Marker -ErrorAction SilentlyContinue)
    if ($MarkerLines.Count -ne 2 -or $MarkerLines[0] -notmatch '^[0-9a-fA-F]{64}$' -or
        $MarkerLines[1] -notmatch '^[0-9a-fA-F]{64}$') { return $false }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Executable).Hash -eq $MarkerLines[1]
}

New-Item -ItemType Directory -Force -Path $CacheDirectory, $InstallParent, $LockDirectory | Out-Null
$LockStream = $null
$Deadline = [DateTime]::UtcNow.AddSeconds($LockWaitSeconds)
while (-not $LockStream) {
    try {
        $LockStream = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $Writer = [System.IO.StreamWriter]::new($LockStream, [System.Text.UTF8Encoding]::new($false), 1024, $true)
        $Writer.WriteLine($PID)
        $Writer.WriteLine([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
        $Writer.Flush()
        $LockStream.Flush($true)
        $Writer.Dispose()
    } catch [System.IO.IOException] {
        $Owner = $null
        $LockLines = @()
        try { $LockLines = @(Get-Content -LiteralPath $LockPath -ErrorAction Stop) } catch {}
        try { $Owner = [int]$LockLines[0] } catch {}
        try { $Started = [long]$LockLines[1] } catch { $Started = 0 }
        $Age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $Started
        if ($Owner -and $Started -gt 0 -and $Age -ge $LockStaleSeconds -and
            -not (Get-Process -Id $Owner -ErrorAction SilentlyContinue)) {
                Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
                continue
        }
        if ([DateTime]::UtcNow -ge $Deadline) {
            throw "Timed out waiting for portable runtime installation lock $LockPath."
        }
        Start-Sleep -Milliseconds 250
    }
}

$ChecksumPart = $null
$ArchivePart = $null
$Staging = $null
try {
    $Force = $Mode -eq 'repair'
    if ($Force -or -not (Test-PortableRuntime)) {
        $ChecksumPart = Join-Path $CacheDirectory "$ArchiveName.sha256.part-$PID"
        $ArchivePart = Join-Path $CacheDirectory "$ArchiveName.part-$PID"
        $Archive = Join-Path $CacheDirectory $ArchiveName
        $Staging = Join-Path $InstallParent ".staging-$ArchiveName-$PID"
        Remove-Item -LiteralPath $ChecksumPart, $ArchivePart, $Staging -Force -Recurse -ErrorAction SilentlyContinue
        Write-Host "Preparing standalone SBK Dashboard $Version for $PlatformId."
        Receive-BoundedFile "$BaseUrl/$ArchiveName.sha256" $ChecksumPart $MaximumChecksumBytes
        $Expected = ((Get-Content -LiteralPath $ChecksumPart | Select-Object -First 1) -split '\s+')[0]
        if ($Expected -notmatch '^[0-9a-fA-F]{64}$') {
            throw "The published checksum for $ArchiveName is invalid."
        }
        $UseCached = (Test-Path -LiteralPath $Archive -PathType Leaf) -and
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash -eq $Expected)
        if (-not $UseCached) {
            Receive-BoundedFile "$BaseUrl/$ArchiveName" $ArchivePart $MaximumArchiveBytes
            $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePart).Hash
            if ($Actual -ne $Expected) { throw "Checksum verification failed for $ArchiveName." }
            Move-Item -LiteralPath $ArchivePart -Destination $Archive -Force
        }

        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $ArchiveObject = [System.IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            $StagingRoot = [System.IO.Path]::GetFullPath($Staging).TrimEnd('\') + '\'
            foreach ($Entry in $ArchiveObject.Entries) {
                $Target = [System.IO.Path]::GetFullPath((Join-Path $Staging $Entry.FullName))
                $UnixType = (($Entry.ExternalAttributes -shr 16) -band 0xF000)
                if (-not $Target.StartsWith($StagingRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
                    $UnixType -eq 0xA000) {
                    throw "Unsafe archive entry: $($Entry.FullName)"
                }
            }
        } finally {
            $ArchiveObject.Dispose()
        }
        New-Item -ItemType Directory -Force -Path $Staging | Out-Null
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Staging)
        $StagedExecutable = Join-Path $Staging "sbk-dashboard-$Version-$PlatformId\sbk-dashboard.exe"
        if (-not (Test-Path -LiteralPath $StagedExecutable -PathType Leaf)) {
            throw 'The portable archive does not contain the expected executable.'
        }
        $ExecutableHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $StagedExecutable).Hash
        Set-Content -LiteralPath (Join-Path $Staging '.installed-sha256') -Value @($Expected, $ExecutableHash) -Encoding ascii
        $Backup = $null
        if (Test-Path -LiteralPath $InstallDirectory) {
            $Backup = Join-Path $InstallParent ".backup-$ArchiveName-$PID"
            [System.IO.Directory]::Move($InstallDirectory, $Backup)
        }
        try {
            [System.IO.Directory]::Move($Staging, $InstallDirectory)
        } catch {
            if ($Backup -and -not (Test-Path -LiteralPath $InstallDirectory)) {
                [System.IO.Directory]::Move($Backup, $InstallDirectory)
            }
            throw
        }
        if ($Backup) { Remove-Item -LiteralPath $Backup -Force -Recurse }
        Remove-Item -LiteralPath $ChecksumPart -Force -ErrorAction SilentlyContinue
    }
} finally {
    if ($ChecksumPart) { Remove-Item -LiteralPath $ChecksumPart -Force -ErrorAction SilentlyContinue }
    if ($ArchivePart) { Remove-Item -LiteralPath $ArchivePart -Force -ErrorAction SilentlyContinue }
    if ($Staging) { Remove-Item -LiteralPath $Staging -Force -Recurse -ErrorAction SilentlyContinue }
    if ($LockStream) { $LockStream.Dispose() }
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}

$env:SBK_DASHBOARD_HOME = $PortableHome
Write-Host "Using standalone SBK Dashboard $Version from $InstallDirectory"
if ($Mode -eq 'repair') {
    Write-Host "Repaired standalone SBK Dashboard $Version runtime."
    exit 0
}
& $Executable $Mode @DashboardArguments
exit $LASTEXITCODE
