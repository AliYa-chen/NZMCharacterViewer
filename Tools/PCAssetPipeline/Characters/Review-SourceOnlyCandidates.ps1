<#
.SYNOPSIS
Review unknown-root and non-SK-named character packages using offline type data.
.DESCRIPTION
Reads every verified archive index, without using SK_ as a mesh type assertion.
Loads candidate packages from the installed base/patch provider, then records
actual types and supplemental source-only mesh findings. This does not assert
current player availability, change project assets, or interact with processes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$GameDirectory,
    [string]$IndexSummary,
    [string]$ConfigurationEvidence,
    [string]$OutputDirectory,
    [string]$CacheDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if(-not $IndexSummary){$IndexSummary=Join-Path $repo 'Saved/PCAssetAnalysis/Indexing/index_summary.json'}
if(-not $ConfigurationEvidence){$ConfigurationEvidence=Join-Path $repo 'Saved/PCAssetAnalysis/ExpandedCharacters/outfit_config_evidence.json'}
if(-not $OutputDirectory){$OutputDirectory=Join-Path $repo 'Saved/PCAssetAnalysis/ExpandedCharacters/ReviewCandidates'}
$game=(Resolve-Path -LiteralPath $GameDirectory).Path.TrimEnd('\','/')
$output=[IO.Path]::GetFullPath($OutputDirectory)
if($output.Equals($game,[StringComparison]::OrdinalIgnoreCase) -or $output.StartsWith($game+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Output must be outside the read-only game tree.'}
$null=[IO.Directory]::CreateDirectory($output)
function Write-Json([string]$Path,$Value){[IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 80),[Text.UTF8Encoding]::new($false))}
function Evidence([string]$Path){$file=Get-Item -LiteralPath $Path;return [ordered]@{file=[IO.Path]::GetRelativePath($repo,$file.FullName).Replace('\','/');bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Package([string]$Path){$value=$Path.Replace('\','/') -replace '^(\.\./)+','';$value=$value -replace '\.uasset$','';if(-not $value.StartsWith('NZM/Content/')){throw ('Unexpected index package: '+$Path)};return '/Game/'+$value.Substring(12)}
$index=Get-Content -LiteralPath $IndexSummary -Raw|ConvertFrom-Json
$configuration=Get-Content -LiteralPath $ConfigurationEvidence -Raw|ConvertFrom-Json
$female=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach($rootName in $configuration.explicit_female_roots){$null=$female.Add($rootName)}
$unknown=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach($rootName in @('Default_Female_00','Hua','Qiang','Xi','Xinyue','Band','Common','Angel_CPN','T0','WanFei','YunFei')){$null=$unknown.Add($rootName)}
$rootFiles=@{};$paths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$indexInputs=[Collections.Generic.List[object]]::new()
foreach($archive in $index.archives){
    if($archive.status -ne 'verified'){throw ('Unverified archive index: '+$archive.archive)}
    $reportFile=[string]$archive.report
    if(-not [IO.Path]::IsPathFullyQualified($reportFile)){$reportFile=Join-Path (Split-Path -Parent $IndexSummary) $reportFile}
    if(-not [IO.File]::Exists($reportFile)){$reportFile=Join-Path (Split-Path -Parent $IndexSummary) ([IO.Path]::GetFileName($archive.report))}
    if(-not [IO.File]::Exists($reportFile)){throw ('Missing verified archive index: '+$archive.report)}
    $indexInputs.Add((Evidence $reportFile))
    $stream=[IO.File]::OpenRead($reportFile)
    try{
        $document=[Text.Json.JsonDocument]::Parse($stream)
        try{
            foreach($entry in $document.RootElement.GetProperty('entries').EnumerateArray()){
                $path=$entry.GetProperty('path').GetString().Replace('\','/')
                if(-not $path.EndsWith('.uasset') -or $path -notmatch '/Avatar/Character/([^/]+)/'){continue}
                $rootName=$Matches[1]
                if($unknown.Contains($rootName)){
                    if(-not $rootFiles.ContainsKey($rootName)){$rootFiles[$rootName]=@{}}
                    if(-not $rootFiles[$rootName].ContainsKey($path)){$rootFiles[$rootName][$path]=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)}
                    $null=$rootFiles[$rootName][$path].Add([string]$archive.archive)
                }
                if($path.Contains('/GenTexDir/')){continue}
                $leaf=[IO.Path]::GetFileName($path)
                if($unknown.Contains($rootName) -or ($female.Contains($rootName) -and $path.Contains('/Mesh/') -and $leaf -notmatch '^(SK_|T_|MI_|VMI_|M_)')){$null=$paths.Add((Package $path))}
            }
        }finally{$document.Dispose()}
    }finally{$stream.Dispose()}
}
$inventoryRoots=[ordered]@{};$counts=[ordered]@{}
foreach($rootName in @($unknown|Sort-Object)){
    $rows=@();if($rootFiles.ContainsKey($rootName)){$rows=@(foreach($path in @($rootFiles[$rootName].Keys|Sort-Object)){[ordered]@{path=$path;archives=@($rootFiles[$rootName][$path]|Sort-Object)}})}
    $inventoryRoots[$rootName]=$rows;$counts[$rootName]=$rows.Count
}
$inventoryPath=Join-Path $output 'candidate_inventory.json'
Write-Json $inventoryPath ([ordered]@{schema_version=1;method='All uasset paths in all verified archive index files, independent of SK naming. Names are candidates; class evidence requires package loading.';source_index_summary=(Evidence $IndexSummary);index_files=@($indexInputs);roots=$inventoryRoots;counts=$counts})
$pathFile=Join-Path $output 'non_sk_review_paths.json'
Write-Json $pathFile @($paths|Sort-Object)
$exporter=Join-Path $PSScriptRoot '../Runtime/Export-Assets.ps1'
$metadataOutput=Join-Path $output 'NonSk'
$arguments=@{InputDirectory=$game;ObjectPaths=@($paths|Sort-Object);OutputDirectory=$metadataOutput;Mode='Metadata'}
if($CacheDirectory){$arguments.CacheDirectory=$CacheDirectory}
& $exporter @arguments|Out-Host
$reportPath=Join-Path $metadataOutput 'export_report.json'
$report=Get-Content -LiteralPath $reportPath -Raw|ConvertFrom-Json
if(@($report.records|Where-Object status -ne 'loaded').Count){throw 'Candidate metadata review contains failed packages.'}
$packageClasses=@{}
foreach($row in $report.records){if(-not $packageClasses.ContainsKey($row.requested_path)){$packageClasses[$row.requested_path]=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)};$null=$packageClasses[$row.requested_path].Add([string]$row.class)}
$supplements=@(
    [ordered]@{package='/Game/Avatar/Character/Default_Female_00/Mesh/SK_Char_DefauIt_Female_00_UI_Body_Mesh';folder='DefaultFemale';skeleton='Char_DefauIt_Female_00_Skeleton';basis='Loaded SkeletalMesh uses Char_DefauIt_Female_00_Skeleton; all five explicit material slots reference Default_Female resources. This supports a source-only female base mesh, not an explicit ModeConfig.Gender result.';availability='current_player_selectability_unverified'},
    [ordered]@{package='/Game/Avatar/Character/Angel/CommonRes/Mesh/Wristband_Mesh';folder='Wristband';skeleton='Wristband_Mesh_Skeleton';basis='Actual loaded SkeletalMesh in an explicitly female Angel resource root, missed by SK_-only discovery. Wrist accessory with independent Wristband_Mesh_Skeleton.';availability='accessory_not_a_separate_outfit'},
    [ordered]@{package='/Game/Avatar/Base/Pendant_Base_Neak';folder='ConfiguredNonSk';skeleton='Male_mesh_3P_Skeleton';basis='Shared base SkeletalMesh explicitly referenced by the female Emma base custom replacement configuration, row 10002000061. Its Male_mesh_3P_Skeleton is retained without attributing female gender to the shared accessory.';availability='shared_configuration_base_not_a_separate_female_outfit'}
)
$findings=[Collections.Generic.List[object]]::new()
foreach($supplement in $supplements){
    $destination=Join-Path $output $supplement.folder
    $arguments=@{InputDirectory=$game;ObjectPaths=@($supplement.package);OutputDirectory=$destination;Mode='Json'}
    if($CacheDirectory){$arguments.CacheDirectory=$CacheDirectory}
    & $exporter @arguments|Out-Host
    $sourceReport=Get-Content -LiteralPath (Join-Path $destination 'export_report.json') -Raw|ConvertFrom-Json
    $record=@($sourceReport.records|Where-Object {$_.status -eq 'loaded' -and $_.class -eq 'SkeletalMesh'})
    if($record.Count -ne 1 -or @($sourceReport.records|Where-Object status -ne 'loaded').Count){throw ('Supplement is not a single verified SkeletalMesh: '+$supplement.package)}
    $jsonPath=Join-Path $destination ('Json/NZM/Content/'+$supplement.package.Substring(6)+'.json')
    $object=Get-Content -LiteralPath $jsonPath -Raw|ConvertFrom-Json
    if($object.Type -ne 'SkeletalMesh' -or $object.Properties.Skeleton.ObjectName -ne ("Skeleton'"+$supplement.skeleton+"'")){throw ('Supplement mesh/skeleton identity changed: '+$supplement.package)}
    $findings.Add([ordered]@{source_package=$supplement.package;classification='verified_SkeletalMesh';basis=$supplement.basis;availability=$supplement.availability;mesh=$record[0].mesh;skeleton=$object.Properties.Skeleton;material_references=$object.SkeletalMaterials;metadata_evidence=(Evidence $jsonPath);source_record=$record[0]})
}
$classRows=@(foreach($path in @($packageClasses.Keys|Sort-Object)){[ordered]@{source_package=$path;loaded_classes=@($packageClasses[$path]|Sort-Object)}})
$summaryPath=Join-Path $output 'review_summary.json'
Write-Json $summaryPath ([ordered]@{schema_version=1;scope='Offline type review of non-SK candidates in explicitly female roots, unknown roster roots, and one shared configuration reference; no process interaction.';status='metadata_review_completed';mounted_archives=$report.mounted_archives;candidate_packages=$packageClasses.Count;loaded_objects=@($report.records).Count;failed_packages=0;inputs=@((Evidence $inventoryPath),(Evidence $pathFile),(Evidence $reportPath));supplemental_mesh_findings=@($findings);package_classes=$classRows;conclusion='Retain one source-only Default_Female base mesh, one Angel Wristband accessory, and a shared Pendant_Base_Neak configuration base. The shared pendant has a male-named skeleton and is not an additional female character. Other reviewed unknown-root files do not supply another female SkeletalMesh. Current roster availability remains unverified.'})
[ordered]@{status='metadata_review_completed';candidate_packages=$packageClasses.Count;loaded_objects=@($report.records).Count;supplemental_meshes=$findings.Count;report=$summaryPath}|ConvertTo-Json -Compress
