<#
.SYNOPSIS
Prepare or verify the exact local DLL runtime recorded in runtime.lock.json.
.DESCRIPTION
Only downloads locked public NuGet packages and the pinned OodleUE archive.
No SDK installation, build hooks, game execution, or process APIs are used.
Original NuGet archives, nuspec files, and bundled licenses remain in Saved.
#>
[CmdletBinding()]
param(
    [ValidateSet('Prepare','Verify','Audit')][string]$Mode = 'Prepare',
    [string]$CacheDirectory,
    [string]$SeedDirectory,
    [switch]$Offline
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $PSVersionTable.PSVersion -lt [version]'7.6' -or [Environment]::Version.Major -lt 10) {
    throw 'This runtime requires Windows, PowerShell 7.6+, and .NET 10+.'
}
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$savedRoot = Join-Path $repositoryRoot 'Saved'
if (-not $CacheDirectory) { $CacheDirectory = Join-Path $savedRoot 'PCAssetAnalysis/ToolRuntime' }
$cacheRoot = [IO.Path]::GetFullPath($CacheDirectory).TrimEnd('\','/')
if (-not $cacheRoot.StartsWith($savedRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'CacheDirectory must be a subdirectory of this repository''s Saved directory.'
}
$lockPath = Join-Path $PSScriptRoot 'runtime.lock.json'
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
if ($lock.schema_version -ne 1) { throw 'Unsupported runtime lock schema.' }
$lockHash = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
$failures = [Collections.Generic.List[string]]::new()
$canPrepare = $Mode -eq 'Prepare'
if ($canPrepare) { [IO.Directory]::CreateDirectory($cacheRoot) | Out-Null }

function Get-SafeCachePath([string]$RelativePath) {
    $resolved = [IO.Path]::GetFullPath((Join-Path $cacheRoot $RelativePath))
    if (-not $resolved.StartsWith($cacheRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
        throw "Lock path escapes the cache: $RelativePath"
    }
    return $resolved
}
function Test-Hash([string]$Path,[string]$Expected) {
    return [IO.File]::Exists($Path) -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ieq $Expected
}
function Get-LockedArchive($Item) {
    $target = Get-SafeCachePath $Item.archive
    if (Test-Hash $target $Item.sha256) { return $target }
    if (-not $canPrepare) { throw "Missing or checksum-mismatched archive: $($Item.archive)" }
    if ($SeedDirectory) {
        $seed = Join-Path ([IO.Path]::GetFullPath($SeedDirectory)) $Item.archive
        if (Test-Hash $seed $Item.sha256) {
            [IO.File]::Copy($seed,$target,$true)
            return $target
        }
    }
    if ($Offline) { throw "Offline preparation needs the locked archive: $($Item.archive)" }
    if ($Item.url -notmatch '^https://(api\.nuget\.org/v3-flatcontainer/|github\.com/WorkingRobot/OodleUE/releases/download/)') {
        throw 'The lock contains an unexpected download host.'
    }
    $partial = $target + '.download'
    for ($attempt=1; $attempt -le 3; $attempt++) {
        try {
            Write-Verbose "Downloading $($Item.archive), attempt $attempt/3"
            Invoke-WebRequest -Uri $Item.url -OutFile $partial -TimeoutSec 90
            if (-not (Test-Hash $partial $Item.sha256)) { throw 'Downloaded SHA256 differs from the lock.' }
            [IO.File]::Move($partial,$target,$true)
            return $target
        } catch {
            if ([IO.File]::Exists($partial)) { [IO.File]::Delete($partial) }
            if ($attempt -eq 3) { throw }
            Start-Sleep -Seconds $attempt
        }
    }
}

foreach ($package in $lock.packages) {
    try {
        $archivePath = Get-LockedArchive $package
        if ((Get-Item -LiteralPath $archivePath).Length -ne $package.size_bytes) { throw 'Archive byte length differs from the lock.' }
        $expandedPath = Get-SafeCachePath $package.directory
        if ($canPrepare) { [IO.Compression.ZipFile]::ExtractToDirectory($archivePath,$expandedPath,$true) }
        $nuspecFiles = @(Get-ChildItem -LiteralPath $expandedPath -Filter '*.nuspec' -File)
        if ($nuspecFiles.Count -ne 1) { throw 'Expected one original nuspec file.' }
        [xml]$nuspec = [IO.File]::ReadAllText($nuspecFiles[0].FullName)
        $metadata = $nuspec.SelectSingleNode("/*[local-name()='package']/*[local-name()='metadata']")
        if ($metadata.SelectSingleNode("*[local-name()='id']").InnerText -ine $package.id -or
            $metadata.SelectSingleNode("*[local-name()='version']").InnerText -ine $package.version) {
            throw 'NuGet package identity differs from the lock.'
        }
    } catch { $failures.Add("$($package.id) $($package.version): $($_.Exception.Message)") }
}
foreach ($native in $lock.external_native) {
    try {
        $archivePath = Get-LockedArchive $native
        $outputPath = Get-SafeCachePath $native.output
        if ($canPrepare) {
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($outputPath)) | Out-Null
            $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
            try {
                $entry = $zip.GetEntry($native.member)
                if ($null -eq $entry) { throw 'Pinned native DLL member was not found.' }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$outputPath,$true)
            } finally { $zip.Dispose() }
        }
        if (-not (Test-Hash $outputPath $native.output_sha256)) { throw 'Native DLL checksum differs from the lock.' }
    } catch { $failures.Add("$($native.name): $($_.Exception.Message)") }
}
$managedEntries = @($lock.managed_assets | ForEach-Object {
    $assetPath = Get-SafeCachePath $_.path
    if (-not (Test-Hash $assetPath $_.sha256)) { $failures.Add("Managed DLL checksum mismatch: $($_.path)") }
    [ordered]@{name=$_.name;version=$_.version;package=$_.package;path=$assetPath}
})
$nativeEntries = @($lock.native_assets | ForEach-Object {
    $assetPath = Get-SafeCachePath $_.path
    if (-not (Test-Hash $assetPath $_.sha256)) { $failures.Add("Native DLL checksum mismatch: $($_.path)") }
    $assetPath
})
$manifestPath = Join-Path $cacheRoot 'runtime_manifest.json'
if ($failures.Count -eq 0 -and $canPrepare) {
    $manifest = [ordered]@{
        schema_version=1;status='locked_assets_verified';lock_sha256=$lockHash;target=$lock.target
        assemblies=$managedEntries;native_libraries=$nativeEntries
        scope='Local offline file parsing only; no game, process, debugger, injection, or anticheat APIs.'
    }
    [IO.File]::WriteAllText($manifestPath,($manifest|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
} elseif ($failures.Count -eq 0) {
    if (-not [IO.File]::Exists($manifestPath)) { $failures.Add('Missing generated runtime_manifest.json; run Prepare first.') }
    else {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        if ($manifest.lock_sha256 -ne $lockHash) { $failures.Add('The cached runtime manifest was generated from a different lock.') }
    }
}
$summary = [ordered]@{
    mode=$Mode;status=$(if($failures.Count-eq0){'verified'}else{'failed'});cache_directory=$cacheRoot
    package_archives=@($lock.packages).Count;managed_dlls=@($lock.managed_assets).Count
    package_native_dlls=@($lock.native_assets).Count;external_native_archives=@($lock.external_native).Count
    failed_checks=$failures.Count;failures=@($failures.ToArray());network_allowed=($canPrepare-and-not$Offline)
}
if ($Mode -eq 'Audit') {
    $summary.licenses = @($lock.packages | Group-Object {$_.license.value} | ForEach-Object {[ordered]@{license=$_.Name;packages=$_.Count}})
}
$summary | ConvertTo-Json -Depth 6
if ($failures.Count -gt 0) { throw "Runtime verification failed with $($failures.Count) issue(s)." }
