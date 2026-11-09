# Read serialized UE 5.8 texture source metadata without loading or changing assets.
# Source.NumMips describes imported source data, not generated/runtime mip residency.
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TextureRoot = '',
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if (-not $TextureRoot) { $TextureRoot = Join-Path $ProjectRoot 'Content/Characters/Textures' }
if (-not $OutputPath) { $OutputPath = Join-Path $ProjectRoot 'Saved/PCAssetAnalysis/current_texture_sources.json' }
$TextureRoot = [IO.Path]::GetFullPath($TextureRoot)
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$projectPrefix = $ProjectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $OutputPath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The output report must be inside ProjectRoot.'
}
if (-not (Test-Path -LiteralPath $TextureRoot -PathType Container)) { throw "Texture directory not found: $TextureRoot" }

function Read-TextureMetadata([IO.FileInfo]$File) {
    # The properties precede bulk source data in the supported editable packages.
    # Bound the read; a larger/different layout is explicitly unsupported.
    $stream = [IO.File]::OpenRead($File.FullName)
    try {
        $readLength = [int][Math]::Min($stream.Length, 1048576)
        $bytes = [byte[]]::new($readLength)
        $read = 0
        while ($read -lt $readLength) {
            $chunk = $stream.Read($bytes, $read, $readLength - $read)
            if ($chunk -eq 0) { throw 'Unexpected end of package.' }
            $read += $chunk
        }
    } finally { $stream.Dispose() }

    if ($bytes.Length -lt 128 -or [BitConverter]::ToString($bytes, 0, 4) -ne 'C1-83-2A-9E') {
        throw 'Unsupported package: missing Unreal editable-package magic.'
    }
    $legacyVersion = [BitConverter]::ToInt32($bytes, 4)
    $ue4Version = [BitConverter]::ToInt32($bytes, 12)
    $ue5Version = [BitConverter]::ToInt32($bytes, 16)
    # This audited layout includes the 20-byte saved hash before TotalHeaderSize.
    if ($legacyVersion -ne -9 -or $ue4Version -ne 522 -or $ue5Version -ne 1018) {
        throw "Unsupported summary versions: legacy=$legacyVersion UE4=$ue4Version UE5=$ue5Version."
    }
    $headerSize = [BitConverter]::ToInt32($bytes, 44)
    $customVersionCount = [BitConverter]::ToInt32($bytes, 48)
    if ($customVersionCount -lt 0 -or $customVersionCount -gt 1024 -or $headerSize -lt 128 -or $headerSize -ge $bytes.Length) {
        throw 'Unsupported or out-of-bounds package summary.'
    }
    $folderOffset = 52 + 20 * $customVersionCount
    if ($folderOffset + 4 -gt $headerSize) { throw 'Package folder string is outside the header.' }
    $folderLength = [BitConverter]::ToInt32($bytes, $folderOffset)
    if ($folderLength -lt 1 -or $folderLength -gt 65536 -or $folderOffset + 20 + $folderLength -gt $headerSize) {
        throw 'Unsupported folder-string encoding or bounds.'
    }
    if ($bytes[$folderOffset + 4 + $folderLength - 1] -ne 0) { throw 'Folder string lacks a terminator.' }
    $nameCountOffset = $folderOffset + 8 + $folderLength
    $nameCount = [BitConverter]::ToInt32($bytes, $nameCountOffset)
    $nameOffset = [BitConverter]::ToInt32($bytes, $nameCountOffset + 4)
    if ($nameCount -lt 1 -or $nameCount -gt 16384 -or $nameOffset -lt $nameCountOffset + 8 -or $nameOffset -ge $headerSize) {
        throw 'Invalid name-map count or offset.'
    }
    $names = [Collections.Generic.List[string]]::new()
    $cursor = $nameOffset
    for ($index = 0; $index -lt $nameCount; $index++) {
        if ($cursor + 4 -gt $headerSize) { throw 'Name-map entry is outside the header.' }
        $length = [BitConverter]::ToInt32($bytes, $cursor)
        $cursor += 4
        if ($length -eq 0 -or [Math]::Abs([long]$length) -gt 65536) { throw 'Unsupported name-map string length.' }
        $byteLength = if ($length -lt 0) { -2 * $length } else { $length }
        $terminatorSize = if ($length -lt 0) { 2 } else { 1 }
        if ($cursor + $byteLength + 4 -gt $headerSize) { throw 'Name-map string or hashes are outside the header.' }
        for ($end = 1; $end -le $terminatorSize; $end++) {
            if ($bytes[$cursor + $byteLength - $end] -ne 0) { throw 'Name-map string lacks a terminator.' }
        }
        $encoding = if ($length -lt 0) { [Text.Encoding]::Unicode } else { [Text.Encoding]::UTF8 }
        $names.Add($encoding.GetString($bytes, $cursor, $byteLength - $terminatorSize))
        $cursor += $byteLength + 4 # Two serialized uint16 name hashes.
    }
    $latin1 = [Text.Encoding]::GetEncoding(28591)
    $binary = $latin1.GetString($bytes)
    if ($binary.Substring(0, $headerSize).IndexOf('++UE5+Release-5.8', [StringComparison]::Ordinal) -lt 0) {
        throw 'Package does not declare the audited UE5 Release-5.8 branch.'
    }

    function Name-Bytes([string]$Name) {
        $nameIndex = $names.IndexOf($Name)
        if ($nameIndex -lt 0) { throw "Required serialized name is absent: $Name" }
        return [byte[]](@([BitConverter]::GetBytes([int]$nameIndex)) + @(0, 0, 0, 0))
    }
    function Struct-Prefix([string]$Property, [string]$Type, [string]$Module) {
        # Complete property type tree: StructProperty -> Type -> Module.
        return [byte[]](@(Name-Bytes $Property) + @(Name-Bytes 'StructProperty') + @(1, 0, 0, 0) +
            @(Name-Bytes $Type) + @(1, 0, 0, 0) + @(Name-Bytes $Module) + @(0, 0, 0, 0))
    }
    function Find-UniqueTag([byte[]]$Prefix, [int]$Start, [int]$End) {
        $needle = $latin1.GetString($Prefix)
        $found = $binary.IndexOf($needle, $Start, $End - $Start, [StringComparison]::Ordinal)
        if ($found -lt 0) { throw "Expected typed property tag is absent between $Start and $End." }
        $nextStart = $found + 1
        if ($binary.IndexOf($needle, $nextStart, $End - $nextStart, [StringComparison]::Ordinal) -ge 0) {
            throw 'Typed property tag is ambiguous; multiple matches found.'
        }
        return $found
    }
    # Restrict tag searches to a small prefix of export data, excluding thumbnails
    # and the name map. No compressed image signature or thumbnail is used.
    $exportPrefixEnd = [int][Math]::Min($bytes.Length, $headerSize + 65536)
    $sourceTag = Find-UniqueTag (Struct-Prefix 'Source' 'TextureSource' '/Script/Engine') $headerSize $exportPrefixEnd
    $sourceSize = [BitConverter]::ToInt32($bytes, $sourceTag + 44)
    $sourceStart = $sourceTag + 49
    $sourceEnd = $sourceStart + $sourceSize
    if ($bytes[$sourceTag + 48] -ne 0 -or $sourceSize -lt 100 -or $sourceSize -gt 65536 -or $sourceEnd -gt $exportPrefixEnd) {
        throw 'Source struct has unsupported flags, length, or bounds.'
    }
    $sourceValues = @{}
    $valueOffsets = @{}
    foreach ($field in @('SizeX', 'SizeY', 'NumMips', 'NumSlices')) {
        # FName + IntProperty type tree (zero children) + size=4 + flags=0.
        $prefix = [byte[]](@(Name-Bytes $field) + @(Name-Bytes 'IntProperty') + @(0, 0, 0, 0, 4, 0, 0, 0, 0))
        $tag = Find-UniqueTag $prefix $sourceStart $sourceEnd
        if ($tag + 29 -gt $sourceEnd) { throw "Source.$field payload exceeds the Source struct." }
        $sourceValues[$field] = [BitConverter]::ToInt32($bytes, $tag + 25)
        $valueOffsets[$field] = $tag + 25
    }
    if ($sourceValues.SizeX -lt 1 -or $sourceValues.SizeY -lt 1 -or $sourceValues.SizeX -gt 65536 -or
        $sourceValues.SizeY -gt 65536 -or $sourceValues.NumMips -lt 1 -or $sourceValues.NumMips -gt 32 -or
        $sourceValues.NumSlices -lt 1 -or $sourceValues.NumSlices -gt 65536) {
        throw 'Source dimensions, source mip count, or source slice count are invalid.'
    }
    $importTag = Find-UniqueTag (Struct-Prefix 'ImportedSize' 'IntPoint' '/Script/CoreUObject') $headerSize $exportPrefixEnd
    if ($importTag + 57 -gt $exportPrefixEnd -or [BitConverter]::ToInt32($bytes, $importTag + 44) -ne 8 -or
        $bytes[$importTag + 48] -ne 8) {
        throw 'ImportedSize has unsupported flags, length, or bounds.'
    }
    $importWidth = [BitConverter]::ToInt32($bytes, $importTag + 49)
    $importHeight = [BitConverter]::ToInt32($bytes, $importTag + 53)
    if ($importWidth -ne $sourceValues.SizeX -or $importHeight -ne $sourceValues.SizeY) {
        throw 'ImportedSize and Source.SizeX/SizeY disagree.'
    }
    [PSCustomObject]@{
        file = [IO.Path]::GetRelativePath($ProjectRoot, $File.FullName).Replace('\', '/')
        file_bytes = $File.Length
        status = 'verified_source_metadata'
        package_versions = [PSCustomObject]@{ legacy = $legacyVersion; ue4 = $ue4Version; ue5 = $ue5Version }
        header_size = $headerSize
        name_map_offset = $nameOffset
        source = [PSCustomObject]@{
            width = $sourceValues.SizeX; height = $sourceValues.SizeY
            source_num_mips = $sourceValues.NumMips; source_num_slices = $sourceValues.NumSlices
            tag_offset = $sourceTag; payload_offset = $sourceStart; serialized_bytes = $sourceSize
            width_value_offset = $valueOffsets.SizeX; height_value_offset = $valueOffsets.SizeY
        }
        imported_size = [PSCustomObject]@{
            width = $importWidth; height = $importHeight; tag_offset = $importTag
            width_value_offset = $importTag + 49; height_value_offset = $importTag + 53
        }
        source_matches_imported_size = $true
    }
}

$assets = [Collections.Generic.List[object]]::new()
$failures = [Collections.Generic.List[object]]::new()
$files = @(Get-ChildItem -LiteralPath $TextureRoot -Recurse -File -Filter '*.uasset' | Sort-Object FullName)
foreach ($file in $files) {
    try { $assets.Add((Read-TextureMetadata $file)) }
    catch {
        $failures.Add([PSCustomObject]@{
            file = [IO.Path]::GetRelativePath($ProjectRoot, $file.FullName).Replace('\', '/')
            file_bytes = $file.Length; status = 'unsupported_or_invalid'; reason = $_.Exception.Message
        })
    }
}
$dimensions = @($assets | Group-Object { '{0}x{1}' -f $_.source.width, $_.source.height } | ForEach-Object {
    [PSCustomObject]@{ width = $_.Group[0].source.width; height = $_.Group[0].source.height; count = $_.Count }
} | Sort-Object width, height)
$report = [ordered]@{
    schema_version = 1
    status = if ($failures.Count -eq 0 -and $assets.Count -gt 0) { 'passed' } else { 'failed' }
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    project_root = $ProjectRoot
    texture_root = $TextureRoot
    method = 'UE5.8 version-checked name map and bounded complete property type tags; Source.SizeX/SizeY crosschecked against ImportedSize.IntPoint'
    limitations = @(
        'Only the explicitly checked editable UE5.8 package layout is supported; unsupported metadata is reported as a failure.',
        'Dimensions describe imported source metadata, not runtime resident or cooked mip dimensions.',
        'Source.NumMips describes imported source data; it is not the generated runtime mip-chain count.',
        'No image pixel decode, visual test, PC source comparison, or original-game extraction is performed.'
    )
    summary = [ordered]@{
        discovered = $files.Count; verified = $assets.Count; failed = $failures.Count
        crosschecked = @($assets | Where-Object source_matches_imported_size).Count
        max_width = if ($assets.Count) { ($assets.source.width | Measure-Object -Maximum).Maximum } else { $null }
        max_height = if ($assets.Count) { ($assets.source.height | Measure-Object -Maximum).Maximum } else { $null }
        dimensions = $dimensions
    }
    assets = @($assets.ToArray())
    failures = @($failures.ToArray())
}
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $OutputPath))
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding utf8
[PSCustomObject]@{ status = $report.status; report = $OutputPath; summary = $report.summary } | ConvertTo-Json -Depth 6
if ($report.status -ne 'passed') { exit 1 }
