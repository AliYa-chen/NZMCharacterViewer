<#
.SYNOPSIS
Read selected PC rendering settings without modifying the game installation.
.DESCRIPTION
Exports an allowlist of local graphics settings and historical quality-setting
log entries. It never saves the full decoded log. Requested values in a log are
not proof of the winning runtime value, shader support, or a rendered frame.
.EXAMPLE
pwsh -File Scripts/inspect_pc_rendering.ps1 -GameDirectory 'Y:\Games\NZM'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$GameDirectory,
    [string]$OutputFile = (Join-Path $PSScriptRoot '../Saved/PCAssetAnalysis/rendering_parameters.json')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$gameRoot = (Resolve-Path -LiteralPath $GameDirectory).Path.TrimEnd('\', '/')
$outputPath = [IO.Path]::GetFullPath($OutputFile)
if ($outputPath.StartsWith($gameRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputFile must be outside the read-only game installation.'
}

$configRoot = Join-Path $gameRoot 'Saved/Config/WindowsClient'
$configNames = @('GameUserSettings.ini', 'NZHDSystemSetting.ini', 'Engine.ini', 'DeviceProfiles.ini', 'Scalability.ini')
$userGraphicsNames = @(
    'Upscaler', 'UpscalerQuality', 'bUseVSync', 'bUseDynamicResolution',
    'ResolutionSizeX', 'ResolutionSizeY', 'FullscreenMode', 'FrameRateLimit',
    'bUseHDRDisplayOutput', 'HDRDisplayOutputNits'
)
$cachedGraphicsNames = @(
    'ReflectionQuality', 'TextureFilterQuality', 'AOQuality', 'ParticleQuality',
    'RefractionQuality', 'DetailMode', 'LODDistance', 'GIQuality', 'ShaderQuality',
    'TextureStreamingPoolSize', 'TextureStreamingQuality', 'ShadowQuality',
    'PostprocessQuality', 'FogQuality', 'AnimationQuality', 'NvClothQuality', 'QualityPreset'
)
$qualityNames = @('QualityLevel', 'FPSLevel', 'bPlayerSetAntiAliasing', 'ScalabilityQualityLevel', 'bAntiAliasing')
$configFiles = [Collections.Generic.List[object]]::new()
$configEntries = [Collections.Generic.List[object]]::new()

foreach ($configName in $configNames) {
    $configPath = Join-Path $configRoot $configName
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        $configFiles.Add([ordered]@{ path = $configPath; status = 'missing' })
        continue
    }
    $lines = [IO.File]::ReadAllLines($configPath)
    $fileEntryStart = $configEntries.Count
    $section = ''
    for ($lineIndex = 0; $lineIndex -lt $lines.Length; $lineIndex++) {
        $rawLine = $lines[$lineIndex]
        $line = $rawLine.Trim()
        if ($line -match '^\[(.+)\]$') {
            $section = $Matches[1]
            continue
        }
        if ($line -notmatch '^([^=]+)=(.*)$') { continue }
        $name = $Matches[1].Trim()
        $value = $Matches[2].Trim()
        $include = $false
        if ($configName -eq 'GameUserSettings.ini') {
            $include = ($section -eq 'ScalabilityGroups' -and $name -match '^sg\.[A-Za-z]+Quality$') -or
                ($section -eq '/Script/GameFramework.NZGameUserSettingsWindows' -and $name -in $userGraphicsNames)
        }
        elseif ($configName -eq 'NZHDSystemSetting.ini' -and $section -eq '/Script/GameFramework.NZHDUserClientGameData') {
            if ($name -eq 'CachedCvarSettings') {
                foreach ($setting in [regex]::Matches($value, '\("(?<name>[A-Za-z0-9_.]+)",\s*(?<value>[A-Za-z0-9_.+-]+)\)')) {
                    if ($setting.Groups['name'].Value -notin $cachedGraphicsNames) { continue }
                    $configEntries.Add([ordered]@{
                        source_file = $configPath
                        source_line_number = $lineIndex + 1
                        source_line = $rawLine
                        section = $section
                        name = $setting.Groups['name'].Value
                        value = $setting.Groups['value'].Value
                        container = 'CachedCvarSettings'
                        evidence_kind = 'saved_user_quality_category'
                    })
                }
            }
            else { $include = $name -in $qualityNames }
        }
        elseif ($configName -in @('Engine.ini', 'DeviceProfiles.ini', 'Scalability.ini')) {
            # Only plain rendering CVars in known rendering sections are retained.
            $include = $section -match '^(SystemSettings|ConsoleVariables|/Script/Engine.RendererSettings|.+Quality@[0-9]+)$' -and
                $name -match '^[+]?((r|sg|fx|foliage)\.)[A-Za-z0-9_.]+$'
        }
        if ($include) {
            $configEntries.Add([ordered]@{
                source_file = $configPath
                source_line_number = $lineIndex + 1
                source_line = $rawLine
                section = $section
                name = $name
                value = $value
                evidence_kind = 'saved_local_configuration'
            })
        }
    }
    $configFiles.Add([ordered]@{
        path = $configPath
        status = 'read'
        bytes = (Get-Item -LiteralPath $configPath).Length
        sha256 = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
        retained_entries = $configEntries.Count - $fileEntryStart
    })
}

$logPath = Join-Path $gameRoot 'Saved/Logs/NZM.log'
$logEntries = [Collections.Generic.List[object]]::new()
$resourceReferences = [Collections.Generic.List[object]]::new()
$resourceTargets = @(
    '/Game/DataTables/PostProcessDOFTable.PostProcessDOFTable',
    '/Game/DataTables/PostProcessEffect.PostProcessEffect',
    '/Game/DataTables/PostProcessRadialBlurTable.PostProcessRadialBlurTable',
    '/Game/Blueprints/TODSystem/NZGameClockConfigTable.NZGameClockConfigTable',
    '/Game/DataTables/SeasonOrnament/SeasonCollectionAvatarSceneConf.SeasonCollectionAvatarSceneConf'
)
$finalValues = [ordered]@{}
$logStatus = 'missing'
$logEncoding = $null
$logHash = $null
if (Test-Path -LiteralPath $logPath -PathType Leaf) {
    $logBytes = [IO.File]::ReadAllBytes($logPath)
    $logText = [Text.Encoding]::UTF8.GetString($logBytes)
    if ($logText.Contains('TrySetCvarQualityLevel Set Cvar')) {
        $logEncoding = 'UTF-8/plain'
    }
    else {
        for ($byteIndex = 0; $byteIndex -lt $logBytes.Length; $byteIndex++) {
            $logBytes[$byteIndex] = $logBytes[$byteIndex] -bxor 0x73
        }
        $logText = [Text.Encoding]::UTF8.GetString($logBytes)
        if ($logText.Contains('TrySetCvarQualityLevel Set Cvar')) { $logEncoding = 'UTF-8/XOR-0x73' }
    }
    $logHash = (Get-FileHash -LiteralPath $logPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($null -eq $logEncoding) {
        $logStatus = 'no_recognized_quality_entries'
    }
    else {
        $logStatus = 'read'
        $logLines = $logText -split '\r?\n'
        $pattern = '^\[(?<timestamp>\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]\[(?<frame>\d+)\].*?UNZUserClientGameData::TrySetCvarQualityLevel Set Cvar (?<name>[A-Za-z0-9_.]+) = (?<value>[-+]?[0-9]+(?:\.[0-9]+)?)\s*$'
        for ($lineIndex = 0; $lineIndex -lt $logLines.Length; $lineIndex++) {
            if ($logLines[$lineIndex].Contains('RequestAsyncLoad called with both valid and null assets') -and
                $logLines[$lineIndex] -match '^\[(?<timestamp>\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]\[(?<frame>\d+)\]') {
                $referenceTime = $Matches['timestamp']
                foreach ($target in $resourceTargets) {
                    if ($logLines[$lineIndex].Contains($target)) {
                        # Retain only the allowlisted path, never the large request list.
                        $resourceReferences.Add([ordered]@{
                            source_file = $logPath
                            source_line_number = $lineIndex + 1
                            timestamp_as_logged = $referenceTime
                            object_path = $target
                            evidence_kind = 'path_in_mixed_valid_and_null_async_request_warning'
                            loaded_successfully = 'not_established'
                        })
                    }
                }
            }
            if ($logLines[$lineIndex] -notmatch $pattern) { continue }
            $entry = [ordered]@{
                source_file = $logPath
                source_line_number = $lineIndex + 1
                source_line = $logLines[$lineIndex]
                timestamp_as_logged = $Matches['timestamp']
                frame_as_logged = [int]$Matches['frame']
                name = $Matches['name']
                value = [double]::Parse($Matches['value'], [Globalization.CultureInfo]::InvariantCulture)
                value_as_logged = $Matches['value']
                evidence_kind = 'historical_quality_setting_request'
            }
            $logEntries.Add($entry)
            $finalValues[$entry.name] = $entry
        }
    }
    # Deliberately discard other decoded data instead of writing a decoded log.
    $logText = $null
    $logLines = $null
    $logBytes = $null
}

$report = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    game_directory = $gameRoot
    status = 'configuration_and_historical_log_only'
    limitations = @(
        'Saved INI files are local user settings, not a complete export of packaged defaults.',
        'Log entries record quality-setting requests during an earlier run, not a current effective CVar dump or frame capture.',
        'last_request_by_name is only the last matching request in this log; later overrides, rejected writes, and shader support are not established.',
        'Numeric quality category and upscaler enum meanings are not inferred.',
        'Per-scene light transforms, colors, intensities, HDR textures, postprocess volumes and particle assets are not extracted by this script.',
        'Allowlisted resource paths in an asynchronous request warning do not establish successful loading or table contents.',
        'No unfiltered configuration, account data, or complete decoded log is retained.'
    )
    config = [ordered]@{
        sources = @($configFiles.ToArray())
        entry_count = $configEntries.Count
        entries = @($configEntries.ToArray())
    }
    log = [ordered]@{
        source_file = $logPath
        status = $logStatus
        sha256 = $logHash
        decoding = $logEncoding
        timestamp_timezone = 'not_established'
        entry_count = $logEntries.Count
        distinct_name_count = $finalValues.Count
        repeated_name_entry_count = $logEntries.Count - $finalValues.Count
        history = @($logEntries.ToArray())
        last_request_by_name = $finalValues
        resource_references = @($resourceReferences.ToArray())
    }
}

$outputDirectory = [IO.Path]::GetDirectoryName($outputPath)
[IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
[IO.File]::WriteAllText($outputPath, ($report | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
[ordered]@{
    output_file = $outputPath
    config_entry_count = $configEntries.Count
    log_status = $logStatus
    log_entry_count = $logEntries.Count
    distinct_cvar_names = $finalValues.Count
    repeated_name_entries = $logEntries.Count - $finalValues.Count
    game_files_modified = $false
} | ConvertTo-Json
