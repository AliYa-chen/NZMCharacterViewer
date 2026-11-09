<#
.SYNOPSIS
Exports the bounded serialized lighting-template closure of the published PC maps.
.DESCRIPTION
Requires already prepared, locked local Runtime dependencies. Reads only the
specified NZM Content/Paks tree. No network, game launch, process enumeration,
memory access or editor operation. Saves host reports in Saved and publishes only
relative JSON evidence to the supplied Environment directory.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$GameDirectory,
    [string]$EnvironmentDirectory,
    [string]$WorkDirectory,
    [string]$CacheDirectory,
    [ValidateRange(1, 30)][int]$MaxPackages = 30,
    [ValidateRange(1, 3)][int]$MaxLayers = 3
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $EnvironmentDirectory) { $EnvironmentDirectory = Join-Path $repositoryRoot 'PCResources/Environment' }
if (-not $WorkDirectory) { $WorkDirectory = Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/PCLightingDependencies' }
$inputPaks = (Resolve-Path -LiteralPath (Join-Path $GameDirectory 'Content/Paks')).Path
[IO.Directory]::CreateDirectory($WorkDirectory) | Out-Null
& (Join-Path $PSScriptRoot 'Get-TemplateSeeds.ps1') -EnvironmentDirectory $EnvironmentDirectory -WorkDirectory $WorkDirectory
$seeds = Get-Content -LiteralPath (Join-Path $WorkDirectory 'template_seeds.json') -Raw | ConvertFrom-Json
$known = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$pending = @($seeds.seed_packages)
$completedLayer = 0
for ($layer = 1; $layer -le $MaxLayers -and $pending.Count -gt 0; $layer++) {
    if ($known.Count + $pending.Count -gt $MaxPackages) {
        throw "The explicit dependency closure exceeds MaxPackages=$MaxPackages; no arbitrary subset was selected. See the preceding closure JSON."
    }
    foreach ($package in $pending) { [void]$known.Add($package) }
    $layerDirectory = Join-Path $WorkDirectory ('Layer' + $layer)
    & (Join-Path $PSScriptRoot '../Runtime/Export-Assets.ps1') -InputDirectory $inputPaks -ObjectPaths $pending -OutputDirectory $layerDirectory -CacheDirectory $CacheDirectory -Mode Json
    $layerReport = Get-Content -LiteralPath (Join-Path $layerDirectory 'export_report.json') -Raw | ConvertFrom-Json
    if (@($layerReport.records | Where-Object status -eq 'failed').Count -gt 0 -or @($layerReport.exports | Where-Object {-not $_.success}).Count -gt 0) {
        throw "Layer $layer has a failed package/export. Its host-only export_report.json remains in Saved; publishing stopped."
    }
    & (Join-Path $PSScriptRoot 'Find-TemplateClosure.ps1') -Layer $layer -WorkDirectory $WorkDirectory
    $closure = Get-Content -LiteralPath (Join-Path $WorkDirectory ('closure_after_layer' + $layer + '.json')) -Raw | ConvertFrom-Json
    $pending = @($closure.next_packages)
    $completedLayer = $layer
}
if ($completedLayer -eq 0) { throw 'No external template seeds were found in the supplied maps.' }
foreach ($oldLayer in @(Get-ChildItem -LiteralPath $WorkDirectory -Directory | Where-Object Name -match '^Layer[1-3]$')) {
    if ([int]$oldLayer.Name.Substring(5) -gt $completedLayer) {
        throw 'WorkDirectory contains a stale later layer; choose a fresh WorkDirectory to avoid mixing exports.'
    }
}
$closureStatus = [ordered]@{
    schema_version = 1
    package_limit = $MaxPackages
    layer_limit = $MaxLayers
    requested_dependency_packages = $known.Count
    completed_layers = $completedLayer
    unresolved_due_to_layer_limit = $pending
    status = $(if ($pending.Count -gt 0) {'bounded_closure_incomplete'} else {'all_discovered_external_template_packages_exported'})
    native_defaults = 'not serialized and not synthesized'
}
[IO.File]::WriteAllText((Join-Path $WorkDirectory 'closure_status.json'), ($closureStatus | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
& (Join-Path $PSScriptRoot 'Export-ObjectIndex.ps1') -GameDirectory $GameDirectory -EnvironmentDirectory $EnvironmentDirectory -WorkDirectory $WorkDirectory -CacheDirectory $CacheDirectory
& (Join-Path $PSScriptRoot 'Resolve-Templates.ps1') -EnvironmentDirectory $EnvironmentDirectory -WorkDirectory $WorkDirectory
[IO.File]::Copy((Join-Path $WorkDirectory 'closure_status.json'), (Join-Path $EnvironmentDirectory 'Dependencies/closure_status.json'), $true)
& (Join-Path $PSScriptRoot 'Validate-Templates.ps1') -EnvironmentDirectory $EnvironmentDirectory
