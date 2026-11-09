[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$GameDirectory,
    [string]$EnvironmentDirectory,
    [string]$WorkDirectory,
    [string]$CacheDirectory
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $EnvironmentDirectory) { $EnvironmentDirectory = Join-Path $repositoryRoot 'PCResources/Environment' }
if (-not $WorkDirectory) { $WorkDirectory = Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/PCLightingDependencies' }
$root = [IO.Path]::GetFullPath($WorkDirectory)
. (Join-Path $repositoryRoot 'Tools/PCAssetPipeline/Runtime/Import-Runtime.ps1') -CacheDirectory $CacheDirectory | Out-Null
[CUE4Parse.Compression.OodleHelper]::Initialize((Join-Path $NZMRuntimeCacheRoot 'Native/oodle-data-shared.dll'))
function New-DefaultedObject([type]$Type, [hashtable]$Overrides = @{}) {
    $constructor = $Type.GetConstructors() | Sort-Object { $_.GetParameters().Count } | Select-Object -First 1
    $parameters = $constructor.GetParameters()
    $arguments = [object[]]::new($parameters.Count)
    for ($i = 0; $i -lt $parameters.Count; $i++) {
        $parameter = $parameters[$i]
        if ($Overrides.ContainsKey($parameter.Name)) { $arguments[$i] = $Overrides[$parameter.Name] }
        elseif ($parameter.HasDefaultValue) { $arguments[$i] = $parameter.DefaultValue }
        elseif ($parameter.ParameterType.IsValueType) { $arguments[$i] = [Activator]::CreateInstance($parameter.ParameterType) }
        else { $arguments[$i] = $null }
    }
    $constructor.Invoke($arguments)
}
function Resolved-Evidence($resolved) {
    if ($null -eq $resolved) { return $null }
    [ordered]@{
        package = $(if ($resolved.Package) { $resolved.Package.Name } else { $null })
        export_index = $resolved.ExportIndex
        name = $resolved.Name.ToString()
        object_path = $resolved.GetPathName()
    }
}
$maps = Get-Content -LiteralPath (Join-Path $EnvironmentDirectory 'scene_parameters.json') -Raw | ConvertFrom-Json
$dependencyPackages = @(Get-ChildItem -LiteralPath $root -Directory | Where-Object Name -match '^Layer[1-3]$' | ForEach-Object {
    $layerReport = Get-Content -LiteralPath (Join-Path $_.FullName 'export_report.json') -Raw | ConvertFrom-Json
    $layerReport.records | Where-Object status -eq 'loaded' | ForEach-Object requested_path
})
$requested = @(@($maps.maps | ForEach-Object virtual_package) + $dependencyPackages | Sort-Object -Unique)
$versions = New-DefaultedObject ([CUE4Parse.UE4.Versions.VersionContainer]) @{game = [CUE4Parse.UE4.Versions.EGame]::GAME_AssaultFireFuture}
$inputRoot = (Resolve-Path -LiteralPath (Join-Path $GameDirectory 'Content/Paks')).Path
$provider = [CUE4Parse.FileProvider.DefaultFileProvider]::new($inputRoot, [IO.SearchOption]::AllDirectories, $versions, [StringComparer]::OrdinalIgnoreCase)
$rows = [Collections.Generic.List[object]]::new()
try {
    $provider.Initialize()
    $placeholder = [CUE4Parse.Encryption.Aes.FAesKey]::new('0x' + ('0' * 64))
    [void]$provider.SubmitKey([Activator]::CreateInstance([CUE4Parse.UE4.Objects.Core.Misc.FGuid]), $placeholder)
    foreach ($request in $requested) {
        $loaded = @($provider.LoadPackageObjects($request))
        if ($loaded.Count -eq 0) { throw ('No package exports: ' + $request) }
        $package = $loaded[0].Owner
        for ($exportIndex = 0; $exportIndex -lt $package.ExportsLazy.Length; $exportIndex++) {
            $object = $package.ExportsLazy[$exportIndex].Value
            if ($null -eq $object) { continue }
            $rows.Add([ordered]@{
                requested_package = $request
                package = $package.Name
                export_index = $exportIndex
                name = $object.Name
                class = $object.ExportType
                object_path = $object.GetPathName()
                outer = Resolved-Evidence $object.Outer
                template = Resolved-Evidence $object.Template
                class_reference = Resolved-Evidence $object.Class
            })
        }
    }
}
finally { $provider.Dispose() }
$output = [ordered]@{
    schema_version = 1
    method = 'CUE4Parse package.ExportsLazy zero-based index, UObject.GetPathName, and ResolvedObject Template/Outer/Class package plus export index; no name-only reference matching'
    source_scope = 'PC Content/Paks base files only; no Saved patches or running game accessed'
    package_count = $requested.Count
    export_count = $rows.Count
    objects = @($rows.ToArray())
}
[IO.File]::WriteAllText((Join-Path $root 'object_index.json'), ($output | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
[ordered]@{packages = $requested.Count; exports = $rows.Count} | ConvertTo-Json
