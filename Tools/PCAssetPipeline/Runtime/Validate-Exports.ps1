[CmdletBinding()]
param([string]$ReportFile,[string]$CacheDirectory)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $ReportFile) { $ReportFile = Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/PCExports/export_report.json' }
$report = Get-Content -LiteralPath $ReportFile -Raw | ConvertFrom-Json
$exportRoot = Split-Path -Parent ([IO.Path]::GetFullPath($ReportFile))
. (Join-Path $PSScriptRoot 'Import-Runtime.ps1') -CacheDirectory $CacheDirectory | Out-Null
[void][NZMOffline.RuntimeResolver]::Load('SkiaSharp')
Add-Type -Path (Join-Path $PSScriptRoot 'Validate-ActorX.cs') -ReferencedAssemblies @($CueParserAssembly.Location,$CueConversionAssembly.Location)
Add-Type -Path (Join-Path $PSScriptRoot 'Validate-Hdr.cs')
[CUE4Parse.Compression.OodleHelper]::Initialize((Join-Path $NZMRuntimeCacheRoot 'Native/oodle-data-shared.dll'))
$versions=[CUE4Parse.UE4.Versions.VersionContainer]::new([CUE4Parse.UE4.Versions.EGame]::GAME_AssaultFireFuture)
$provider=[CUE4Parse.FileProvider.DefaultFileProvider]::new($report.input_root,[IO.SearchOption]::AllDirectories,$versions,[StringComparer]::OrdinalIgnoreCase)
$results=[Collections.Generic.List[object]]::new()
try {
    $provider.Initialize()
    [void]$provider.SubmitKey([Activator]::CreateInstance([CUE4Parse.UE4.Objects.Core.Misc.FGuid]),[CUE4Parse.Encryption.Aes.FAesKey]::new('0x'+('0'*64)))
    $optionsConstructor=[CUE4Parse_Conversion.Options.ExportOptions].GetConstructors()[0]
    $optionsParameters=$optionsConstructor.GetParameters()
    $arguments=[object[]]::new($optionsParameters.Count)
    for($i=0;$i-lt$arguments.Length;$i++){$arguments[$i]=$optionsParameters[$i].DefaultValue}
    $options=$optionsConstructor.Invoke($arguments)
    foreach($export in $report.exports) {
        if ($export.kind -notin @('Meshes','Textures')) { continue }
        if (-not $export.success) { continue }
        foreach($file in $export.files) {
            if ($export.kind -eq 'Textures' -and [IO.Path]::GetExtension($file) -notin @('.png','.hdr')) { continue }
            try {
                $record=$report.records | Where-Object {
                    $_.status -eq 'loaded' -and $_.PSObject.Properties['object_path'] -and $_.object_path -eq $export.object_path
                } | Select-Object -First 1
                if ($null -eq $record) { throw 'Successful export has no matching loaded source record.' }
                if($export.kind-eq'Meshes') {
                    $mesh=@($provider.LoadPackageObjects($record.requested_path)) | Where-Object {
                        $_ -is [CUE4Parse.UE4.Assets.Exports.Engine.USkinnedAsset] -and $_.Name -eq $record.name
                    } | Select-Object -First 1
                    if ($null -eq $mesh) { throw 'Exact source mesh export was not found for ActorX validation.' }
                    $dto=[CUE4Parse_Conversion.Dto.SkeletalMeshDto]::new($mesh,$options)
                    try { $validation=[NZMOffline.ActorXValidator]::Validate($file,$dto) } finally { $dto.Dispose() }
                    $results.Add([ordered]@{kind='ActorX';file=$file;object_path=$export.object_path;status='pass';checks=$validation})
                } elseif($export.kind-eq'Textures' -and [IO.Path]::GetExtension($file)-eq'.png') {
                    $bitmap=[SkiaSharp.SKBitmap]::Decode($file)
                    try {
                        if($null-eq$bitmap){throw 'PNG could not be decoded.'}
                        $highest=$record.texture.mips[$record.texture.highest_available_mip_index]
                        if($bitmap.Width-ne$highest.width -or $bitmap.Height-ne$highest.height){throw 'PNG dimensions differ from highest available source mip.'}
                        $results.Add([ordered]@{kind='PNG';file=$file;object_path=$export.object_path;status='pass';width=$bitmap.Width;height=$bitmap.Height})
                    } finally { if($null-ne$bitmap){$bitmap.Dispose()} }
                } elseif($export.kind-eq'Textures' -and [IO.Path]::GetExtension($file)-eq'.hdr') {
                    $hdr=[NZMOffline.HdrValidator]::Validate($file)
                    $results.Add([ordered]@{kind='HDR';file=$file;object_path=$export.object_path;status='pass';checks=$hdr;validation_scope='Radiance header and complete RLE pixel payload; projection and RGBE values are not compared against source cubemap.'})
                }
            } catch { $results.Add([ordered]@{kind=$export.kind;file=$file;object_path=$export.object_path;status='failed';error=$_.Exception.ToString()}) }
        }
    }
} finally { $provider.Dispose() }
$output=[ordered]@{schema_version=1;generated_at_utc=[DateTime]::UtcNow.ToString('o');scope='Offline data checks: ActorX points and every nonzero weight/index exactly compared against decoded source DTO; PNG decoded and dimensions checked against highest available source mip; HDR header and complete RLE pixel payload checked without source-cubemap pixel comparison. No UE import, rig animation, material appearance or device validation.';results=@($results.ToArray())}
[IO.File]::WriteAllText((Join-Path $exportRoot 'export_validation.json'),($output|ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
[ordered]@{checked=$results.Count;passed=@($results.ToArray()|Where-Object {$_['status']-eq'pass'}).Count;failed=@($results.ToArray()|Where-Object {$_['status']-eq'failed'}).Count;report=(Join-Path $exportRoot 'export_validation.json')}|ConvertTo-Json -Compress
