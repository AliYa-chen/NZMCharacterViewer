<#
.SYNOPSIS
Builds the offline NZM file-cipher adapter from the bundled Apache-2.0 sources.
.DESCRIPTION
Requires PowerShell 7.6 / .NET 10. Reads source files and writes a local DLL.
Does not inspect running processes, access process memory, or launch a game.
The minimal reader type supplies the non-null marker required by the upstream
API; the game-specific algorithm uses its upstream fixed schedule.
#>
[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))) 'Saved/PCAssetAnalysis/ToolRuntime/Cipher')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion -lt [version]'7.6') { throw 'PowerShell 7.6 or newer is required.' }
$upstream = Join-Path $PSScriptRoot 'Upstream'
$cipherSourcePath = Join-Path $upstream 'AssaultFireFutureAes.cs'
$tableSourcePath = Join-Path $upstream 'NetEaseAes.cs'
$cipherSource = [IO.File]::ReadAllText($cipherSourcePath)
$tableSource = [IO.File]::ReadAllText($tableSourcePath)
$sourceId = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($cipherSource + $tableSource))).Substring(0, 16).ToLowerInvariant()
$output = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($output) | Out-Null
$assemblyPath = Join-Path $output ("NZMOfflineCipher-$sourceId.dll")
if (-not (Test-Path -LiteralPath $assemblyPath -PathType Leaf)) {
    $tables = [Collections.Generic.List[string]]::new()
    foreach ($name in @('Td0', 'Td1', 'Td2', 'Td3', 'Td4')) {
        $match = [regex]::Match($tableSource, 'public static readonly uint\[\] ' + $name + '\s*=\s*\[[\s\S]*?\];')
        if (-not $match.Success) { throw "Upstream lookup table missing: $name" }
        $tables.Add($match.Value)
    }
    $lut = 'namespace CUE4Parse.GameTypes.NetEase.MAR.Encryption.Aes { public static class NetEaseAes {' + ($tables -join "`n") + '} }'
    $stub = 'namespace CUE4Parse.UE4.VirtualFileSystem { public interface IAesVfsReader { object AesKey {get;} } public class ProbeReader : IAesVfsReader { public object AesKey {get;} = new object(); } }'
    $cipher = "#nullable enable`nusing System;`n" + $cipherSource
    $lutPath = Join-Path $output 'LookupTables.cs'
    $stubPath = Join-Path $output 'ReaderMarker.cs'
    $adaptedPath = Join-Path $output 'AdaptedCipher.cs'
    [IO.File]::WriteAllText($lutPath, $lut)
    [IO.File]::WriteAllText($stubPath, $stub)
    [IO.File]::WriteAllText($adaptedPath, $cipher)
    Add-Type -Path @($lutPath, $stubPath, $adaptedPath) -OutputAssembly $assemblyPath
}
$null = [Reflection.AssemblyName]::GetAssemblyName($assemblyPath)
$proof = [ordered]@{
    source_id = $sourceId
    sources = @(
        @{ file = 'Upstream/AssaultFireFutureAes.cs'; sha256 = (Get-FileHash -LiteralPath $cipherSourcePath -Algorithm SHA256).Hash.ToLowerInvariant() },
        @{ file = 'Upstream/NetEaseAes.cs'; sha256 = (Get-FileHash -LiteralPath $tableSourcePath -Algorithm SHA256).Hash.ToLowerInvariant() }
    )
    assembly_file = [IO.Path]::GetFileName($assemblyPath)
    assembly_sha256 = (Get-FileHash -LiteralPath $assemblyPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
[IO.File]::WriteAllText((Join-Path $output 'build_manifest.json'), ($proof | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
Write-Output $assemblyPath
