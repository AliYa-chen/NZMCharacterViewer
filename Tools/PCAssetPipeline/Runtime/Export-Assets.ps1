[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InputDirectory,
    [Parameter(Mandatory=$true)][string[]]$ObjectPaths,
    [string]$OutputDirectory,
    [string]$CacheDirectory,
    [ValidateSet('Metadata','Json','JsonAndTextures')][string]$Mode = 'Metadata',
    [switch]$ExportMeshes,
    [string]$OodleLibrary,
    [string]$ZlibLibrary
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/PCExports' }
$inputRoot = (Resolve-Path -LiteralPath $InputDirectory).Path.TrimEnd('\','/')
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
if ($outputRoot.Equals($inputRoot,[StringComparison]::OrdinalIgnoreCase) -or
    $outputRoot.StartsWith($inputRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output must be outside the read-only input tree.'
}
[IO.Directory]::CreateDirectory($outputRoot) | Out-Null
. (Join-Path $PSScriptRoot 'Import-Runtime.ps1') -CacheDirectory $CacheDirectory | Out-Null

function New-DefaultedObject([type]$Type, [hashtable]$Overrides = @{}) {
    $constructor = $Type.GetConstructors() | Sort-Object { $_.GetParameters().Count } | Select-Object -First 1
    $parameters = $constructor.GetParameters()
    $arguments = [object[]]::new($parameters.Count)
    for ($i=0; $i -lt $parameters.Count; $i++) {
        $parameter = $parameters[$i]
        if ($Overrides.ContainsKey($parameter.Name)) { $arguments[$i] = $Overrides[$parameter.Name] }
        elseif ($parameter.HasDefaultValue) { $arguments[$i] = $parameter.DefaultValue }
        elseif ($parameter.ParameterType.IsValueType) { $arguments[$i] = [Activator]::CreateInstance($parameter.ParameterType) }
        else { $arguments[$i] = $null }
    }
    return $constructor.Invoke($arguments)
}

function Get-PackageSources($Provider, [string]$PackageName) {
    $fixedPath = $Provider.FixPath($PackageName)
    if ($fixedPath.EndsWith('.uasset') -or $fixedPath.EndsWith('.umap')) { $fixedPath = $fixedPath.Substring(0,$fixedPath.Length-7) }
    $sources = [ordered]@{}
    foreach ($extension in @('uasset','umap','uexp','ubulk','uptnl')) {
        [CUE4Parse.FileProvider.Objects.GameFile]$gameFile = $null
        if ($Provider.TryGetGameFile($fixedPath + '.' + $extension, [ref]$gameFile)) {
            $source = [ordered]@{virtual_path=$gameFile.Path;size=$gameFile.Size;compression=$gameFile.CompressionMethod.ToString()}
            if ($gameFile -is [CUE4Parse.UE4.Pak.Objects.FPakEntry]) {
                $source.archive=$gameFile.Vfs.Path
                $source.provider_read_order=$gameFile.Vfs.ReadOrder
                $source.archive_offset=$gameFile.Offset
            }
            $sources[$extension]=$source
        }
    }
    return $sources
}

if (-not $OodleLibrary) { $OodleLibrary = Join-Path $NZMRuntimeCacheRoot 'Native/oodle-data-shared.dll' }
if (-not (Test-Path -LiteralPath $OodleLibrary -PathType Leaf)) { throw 'Missing local Oodle DLL; run Prepare-Runtime.ps1.' }
[CUE4Parse.Compression.OodleHelper]::Initialize([IO.Path]::GetFullPath($OodleLibrary))
if ($ZlibLibrary) {
    if (-not (Test-Path -LiteralPath $ZlibLibrary -PathType Leaf)) { throw 'ZlibLibrary must name an existing local DLL.' }
    [CUE4Parse.Compression.ZlibHelper]::Initialize([IO.Path]::GetFullPath($ZlibLibrary))
}
$versions = New-DefaultedObject ([CUE4Parse.UE4.Versions.VersionContainer]) @{game=[CUE4Parse.UE4.Versions.EGame]::GAME_AssaultFireFuture}
$provider = [CUE4Parse.FileProvider.DefaultFileProvider]::new($inputRoot, [IO.SearchOption]::AllDirectories, $versions, [StringComparer]::OrdinalIgnoreCase)
$records = [Collections.Generic.List[object]]::new()
$exportResults = [Collections.Generic.List[object]]::new()
try {
    $provider.Initialize()
    # The upstream AssaultFireFuture override uses its fixed game-specific schedule.
    # Its API still requires a non-null FAesKey. No supplied/user key is stored.
    $placeholder = [CUE4Parse.Encryption.Aes.FAesKey]::new('0x' + ('0' * 64))
    $mountedCount = $provider.SubmitKey([Activator]::CreateInstance([CUE4Parse.UE4.Objects.Core.Misc.FGuid]), $placeholder)
    $options = New-DefaultedObject ([CUE4Parse_Conversion.Options.ExportOptions]) @{exportMaterials=$false;exportMorphTargets=$false}
    $jsonSession = New-DefaultedObject ([CUE4Parse_Conversion.ExportSession])
    $textureSession = New-DefaultedObject ([CUE4Parse_Conversion.ExportSession])
    $meshSession = New-DefaultedObject ([CUE4Parse_Conversion.ExportSession])
    $meshOptions = New-DefaultedObject ([CUE4Parse_Conversion.Options.ExportOptions]) @{meshFormat=[CUE4Parse_Conversion.Options.EMeshFormat]::ActorX;exportMaterials=$false;exportMorphTargets=$false;socketFormat=[CUE4Parse_Conversion.Options.ESocketFormat]::None}
    $jsonSession.MaxDegreeOfParallelism = 1
    $textureSession.MaxDegreeOfParallelism = 1
    $meshSession.MaxDegreeOfParallelism = 1
    foreach ($objectPath in $ObjectPaths) {
        try {
            $exports = @($provider.LoadPackageObjects($objectPath))
            if ($exports.Count -eq 0) { throw 'Package contains no loaded exports.' }
            foreach ($loadedExport in $exports) {
                $record = [ordered]@{requested_path=$objectPath;name=$loadedExport.Name;class=$loadedExport.ExportType;object_path=$loadedExport.GetPathName();status='loaded';selected_package_files=(Get-PackageSources $provider $loadedExport.Owner.Name)}
                if ($loadedExport -is [CUE4Parse.UE4.Assets.Exports.Texture.UTexture]) {
                    $platformData = $loadedExport.PlatformData
                    $record.texture = [ordered]@{
                        width=$platformData.SizeX;height=$platformData.SizeY;pixel_format=$platformData.PixelFormat
                        compression_settings=$loadedExport.CompressionSettings.ToString();srgb=$loadedExport.SRGB;is_normal_map=$loadedExport.IsNormalMap
                        png_channel_policy='DesktopMobile decode; preserves decoded R/G channels, reconstructs normal Z where required; no green-channel flip or glTF conversion.'
                        first_mip_to_serialize=$platformData.FirstMipToSerialize
                        mips=@($platformData.Mips | ForEach-Object {
                            $header=$_.BulkData.Header
                            $flags=$header.BulkDataFlags.ToString()
                            $payloadHint=if($flags -match 'OptionalPayload'){'uptnl'}elseif($flags -match 'PayloadInSep[ae]rateFile'){'ubulk'}else{'inline_package'}
                            [ordered]@{width=$_.SizeX;height=$_.SizeY;depth=$_.SizeZ;bulk_flags=$flags;bulk_size=$header.SizeOnDisk;bulk_offset=$header.OffsetInFile;payload_file_hint=$payloadHint}
                        })
                        highest_available_mip_index=$loadedExport.GetFirstMipIndex()
                    }
                    if ($Mode -eq 'JsonAndTextures') { [void]$textureSession.Add([CUE4Parse_Conversion.Exporters.TextureExporter]::new($loadedExport)) }
                }
                if ($loadedExport -is [CUE4Parse.UE4.Assets.Exports.Engine.USkinnedAsset]) {
                    $meshDto = [CUE4Parse_Conversion.Dto.SkeletalMeshDto]::new($loadedExport,$options)
                    try {
                        $record.mesh=[ordered]@{
                            reference_bones=$loadedExport.ReferenceSkeleton.FinalRefBoneInfo.Length
                            material_slots=$loadedExport.SkeletalMaterials.Length
                            source_lod_count=$loadedExport.LODModels.Length
                            source_lods=@($loadedExport.LODModels | ForEach-Object {
                                $triangleCount=0; foreach($section in $_.Sections){$triangleCount+=$section.NumTriangles}
                                [ordered]@{vertices=$_.NumVertices;triangles=$triangleCount;sections=$_.Sections.Length;texcoords=$_.NumTexCoords}
                            })
                            inspected_lods=@($meshDto.LODs | ForEach-Object {
                                $maxInfluences=0; $moreThanFour=0
                                foreach($vertex in $_.Vertices){$influenceCount=$vertex.Influences.Length;if($influenceCount -gt $maxInfluences){$maxInfluences=$influenceCount};if($influenceCount -gt 4){$moreThanFour++}}
                                [ordered]@{vertices=$_.Vertices.Length;indices=$_.Indices.Length;sections=$_.Sections.Length;maximum_nonzero_influences=$maxInfluences;vertices_over_four_influences=$moreThanFour}
                            })
                        }
                    } finally { $meshDto.Dispose() }
                    if ($ExportMeshes) { [void]$meshSession.Add([CUE4Parse_Conversion.Exporters.SkinnedAssetExporter]::new($loadedExport)) }
                }
                if ($Mode -ne 'Metadata') { [void]$jsonSession.Add([CUE4Parse_Conversion.Exporters.JsonPropertiesExporter]::new($loadedExport)) }
                $records.Add($record)
            }
        } catch {
            $records.Add([ordered]@{requested_path=$objectPath;status='failed';error=$_.Exception.ToString()})
        }
    }
    foreach ($sessionSpec in @(@{name='Json';session=$jsonSession;options=$options},@{name='Textures';session=$textureSession;options=$options},@{name='Meshes';session=$meshSession;options=$meshOptions})) {
        if (-not $sessionSpec.session.HasQueuedItems) { continue }
        $sessionDirectory = Join-Path $outputRoot $sessionSpec.name
        $sessionResults = $sessionSpec.session.RunAsync($sessionDirectory, $sessionSpec.options, $null, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
        foreach ($result in $sessionResults) {
            $exportResults.Add([ordered]@{kind=$sessionSpec.name;success=$result.Success;object_path=$result.ObjectPath;files=@($result.DiskFilePaths);error=$(if($null-ne$result.Error){$result.Error.ToString()}else{$null})})
        }
    }
    $report = [ordered]@{
        schema_version=1;generated_at_utc=[DateTime]::UtcNow.ToString('o');input_root=$inputRoot
        scope='Offline file parsing/export only. No process, memory, debugging, injection, game launch or anticheat operations.'
        format='CUE4Parse GAME_AssaultFireFuture';mode=$Mode;mounted_archives=$mountedCount
        files_in_provider=$provider.Files.Count;priority_scope='Sources are selected by this provider for the stated input tree. Provider order is recorded, not independently verified against a live game.';records=@($records.ToArray());exports=@($exportResults.ToArray())
    }
    [IO.File]::WriteAllText((Join-Path $outputRoot 'export_report.json'), ($report | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    [ordered]@{report=(Join-Path $outputRoot 'export_report.json');mounted_archives=$mountedCount;provider_files=$provider.Files.Count;loaded=@($records.ToArray()|Where-Object status -eq 'loaded').Count;failed=@($records.ToArray()|Where-Object status -eq 'failed').Count;exports=$exportResults.Count} | ConvertTo-Json -Compress
} finally {
    $provider.Dispose()
}
