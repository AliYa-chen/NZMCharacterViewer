<#
.SYNOPSIS
Export the bounded, evidence-selected character shading dependencies.
.DESCRIPTION
Reads only offline Pak files and reuses the locked Runtime cache. No process,
memory, debugger, injection, game launch, or anticheat APIs are called.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PakDirectory,
    [string]$CacheDirectory,
    [string]$ExtractionDirectory,
    [switch]$Publish
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if(-not$ExtractionDirectory){$ExtractionDirectory=Join-Path $repo 'Saved/PCAssetAnalysis/PCShadingDependencies'}
$selection=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'dependency_seeds.json') -Raw|ConvertFrom-Json
$targets=@($selection.packages.virtual_package|Sort-Object -Unique)
if($targets.Count-ne20 -or $targets.Count-gt$selection.maximum_package_count){throw 'Unexpected or over-budget shading seed list.'}
$runtime=Join-Path $PSScriptRoot '../Runtime/Export-Assets.ps1'
& $runtime -InputDirectory $PakDirectory -ObjectPaths $targets -CacheDirectory $CacheDirectory -OutputDirectory (Join-Path $ExtractionDirectory 'Export') -Mode JsonAndTextures
$report=Get-Content -LiteralPath (Join-Path $ExtractionDirectory 'Export/export_report.json') -Raw|ConvertFrom-Json
if(@($report.records|Where-Object status -ne 'loaded').Count-gt0 -or @($report.exports|Where-Object success -ne $true).Count-gt0){throw 'A shading dependency could not be extracted. Inspect the local report; do not publish incomplete success.'}
if($Publish){& (Join-Path $PSScriptRoot 'Build-Readiness.ps1') -ExtractionDirectory $ExtractionDirectory}
