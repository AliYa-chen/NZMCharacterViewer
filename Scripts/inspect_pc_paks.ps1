<#
.SYNOPSIS
Writes a read-only inventory of the installed PC game's Pak metadata.
.DESCRIPTION
Reads only the final 221 bytes of each Pak under Content/Paks and Saved.
Does not decrypt indexes, list packaged assets, or detect model/texture quality.
Supports NZM's AssaultFireFuture Pak footer: magic 0x4F6FAE86, version 10,
16-byte key GUID, one-byte encrypted-index flag, and five compression names.
The two 64-bit index fields are stored size first, offset second in this game.

Format reference: CUE4Parse FPakInfo, GAME_AssaultFireFuture branch:
https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse/UE4/Pak/Objects/FPakInfo.cs
The parser intentionally reports other layouts as unsupported instead of
guessing their field order. A valid footer does not establish that an index
can be decrypted or that any particular character exists in the archive.
.EXAMPLE
pwsh -File Scripts/inspect_pc_paks.ps1 -GameRoot 'Y:\network-games\NZM'
.EXAMPLE
pwsh -File Scripts/inspect_pc_paks.ps1 -GameRoot 'Y:\network-games\NZM' -OutputPath Saved/PCAssetAnalysis/pak_inventory.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$GameRoot,
    [string]$OutputPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'Saved/PCAssetAnalysis/pak_inventory.json')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-HexString {
    param([byte[]]$Bytes)
    return [BitConverter]::ToString($Bytes).Replace('-', '')
}

function Read-PakFooter {
    param([System.IO.FileInfo]$File, [string]$SourceGroup, [string]$Root)

    $relativePath = $File.FullName.Substring($Root.Length).TrimStart([char[]]'\/').Replace('\', '/')
    $entry = [ordered]@{
        relative_path = $relativePath
        source_group = $SourceGroup
        size_bytes = [long]$File.Length
        status = 'error'
        valid = $false
        issues = @()
    }
    $stream = $null
    $reader = $null
    try {
        if ($File.Length -lt 221) {
            throw 'File is shorter than the supported 221-byte footer.'
        }
        $stream = [IO.File]::Open($File.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        $entry.size_bytes = [long]$stream.Length
        $footerOffset = $stream.Length - 221
        $null = $stream.Seek($footerOffset, [IO.SeekOrigin]::Begin)
        $reader = [IO.BinaryReader]::new($stream)
        $guidBytes = $reader.ReadBytes(16)
        $encryptedFlag = $reader.ReadByte()
        $magic = $reader.ReadUInt32()
        $version = $reader.ReadInt32()
        $entry.footer_offset = [long]$footerOffset
        $entry.footer_size = 221
        $entry.magic = '0x{0:X8}' -f $magic
        $entry.version = $version
        $entry.encrypted_index_flag = [int]$encryptedFlag
        $entry.encryption_key_guid_hex = ConvertTo-HexString $guidBytes
        if ($magic -ne [uint32]0x4F6FAE86 -or $version -ne 10) {
            $entry.status = 'unsupported'
            $entry.issues = @('Unsupported magic/version; index fields were not interpreted.')
            return [pscustomobject]$entry
        }

        $indexSize = $reader.ReadInt64()
        $indexOffset = $reader.ReadInt64()
        $indexHash = $reader.ReadBytes(20)
        $methods = @()
        for ($methodIndex = 0; $methodIndex -lt 5; $methodIndex++) {
            $methodBytes = $reader.ReadBytes(32)
            if ($methodBytes.Length -ne 32) {
                throw 'Compression method table was truncated.'
            }
            $method = [Text.Encoding]::ASCII.GetString($methodBytes).TrimEnd([char]0)
            if ($method.Length -gt 0) { $methods += $method }
        }
        $issues = @()
        if ($encryptedFlag -ne 0 -and $encryptedFlag -ne 1) {
            $issues += 'Encrypted-index flag is neither 0 nor 1.'
        }
        if ($indexOffset -lt 0 -or $indexOffset -gt $footerOffset) {
            $issues += 'Index offset lies outside the data preceding the footer.'
        }
        if ($indexSize -le 0 -or $indexSize -gt $footerOffset) {
            $issues += 'Index size is not a positive length within the archive.'
        }
        # Subtraction avoids overflowing a signed 64-bit value for malformed input.
        if ($indexOffset -ge 0 -and $indexOffset -le $footerOffset -and $indexSize -gt 0) {
            if ($indexSize -gt ($footerOffset - $indexOffset)) {
                $issues += 'Index extends into or beyond the footer.'
            }
        }
        $entry.index_offset = $indexOffset
        $entry.index_size = $indexSize
        $entry.index_sha1_from_footer = ConvertTo-HexString $indexHash
        $entry.index_hash_verified = $false
        $entry.index_encrypted = ($encryptedFlag -eq 1)
        $entry.compression_methods = @($methods)
        $entry.issues = @($issues)
        $entry.valid = ($issues.Count -eq 0)
        if ($entry.valid) { $entry.status = 'valid_footer' } else { $entry.status = 'invalid_footer' }
    }
    catch {
        $entry.status = 'error'
        $entry.valid = $false
        $entry.issues = @($_.Exception.Message)
    }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
    }
    return [pscustomobject]$entry
}

$resolvedRoot = (Get-Item -LiteralPath $GameRoot -ErrorAction Stop).FullName.TrimEnd([char[]]'\/')
if (-not [IO.Directory]::Exists($resolvedRoot)) { throw 'GameRoot must be a directory.' }
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
$rootPrefix = $resolvedRoot + [IO.Path]::DirectorySeparatorChar
if ($resolvedOutput.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedOutput.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputPath must be outside GameRoot; the game installation is read-only.'
}

$entries = @()
$discoveryFailures = @()
foreach ($source in @(
    @{ group = 'content'; path = (Join-Path $resolvedRoot 'Content/Paks') },
    @{ group = 'saved'; path = (Join-Path $resolvedRoot 'Saved') }
)) {
    if (-not [IO.Directory]::Exists($source.path)) {
        $discoveryFailures += [pscustomobject]@{ source_group = $source.group; path = $source.path; error = 'Directory does not exist.' }
        continue
    }
    $enumerationErrors = @()
    $files = @(Get-ChildItem -LiteralPath $source.path -Filter '*.pak' -File -Recurse -ErrorAction SilentlyContinue -ErrorVariable enumerationErrors | Sort-Object FullName)
    foreach ($enumerationError in $enumerationErrors) {
        $discoveryFailures += [pscustomobject]@{ source_group = $source.group; path = [string]$enumerationError.TargetObject; error = $enumerationError.Exception.Message }
    }
    foreach ($file in $files) {
        $entries += Read-PakFooter -File $file -SourceGroup $source.group -Root $resolvedRoot
    }
}

$validEntries = @($entries | Where-Object { $_.valid })
$encryptedEntries = @($validEntries | Where-Object { $_.index_encrypted })
$totalBytes = [long]0
foreach ($entry in $entries) { $totalBytes += $entry.size_bytes }
$report = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    game_root = $resolvedRoot
    inspection = 'Read-only footer metadata; indexes were not decrypted and packaged asset quality was not assessed.'
    format_reference = 'https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse/UE4/Pak/Objects/FPakInfo.cs'
    summary = [ordered]@{
        archive_count = $entries.Count
        total_bytes = $totalBytes
        valid_footer_count = $validEntries.Count
        encrypted_index_count = $encryptedEntries.Count
        unencrypted_index_count = $validEntries.Count - $encryptedEntries.Count
        failed_archive_count = $entries.Count - $validEntries.Count
        discovery_failure_count = $discoveryFailures.Count
    }
    discovery_failures = @($discoveryFailures)
    archives = @($entries)
}
$outputDirectory = Split-Path -Parent $resolvedOutput
$null = [IO.Directory]::CreateDirectory($outputDirectory)
[IO.File]::WriteAllText($resolvedOutput, ($report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Output ('Inventory: {0} archives, {1} valid footers, {2} encrypted indexes, {3} archive failures, {4} discovery failures.' -f $entries.Count, $validEntries.Count, $encryptedEntries.Count, ($entries.Count - $validEntries.Count), $discoveryFailures.Count)
Write-Output ('Report: {0}' -f $resolvedOutput)
if (($entries.Count - $validEntries.Count) -gt 0 -or $discoveryFailures.Count -gt 0) { exit 1 }
