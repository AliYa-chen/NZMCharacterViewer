[CmdletBinding()]
param([string]$EnvironmentDirectory, [string]$WorkDirectory)
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $EnvironmentDirectory) { $EnvironmentDirectory = Join-Path $repositoryRoot 'PCResources/Environment' }
if (-not $WorkDirectory) { $WorkDirectory = Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/PCLightingDependencies' }
$root = [IO.Path]::GetFullPath((Join-Path $EnvironmentDirectory 'Json'))
$summary = Get-Content -LiteralPath (Join-Path $EnvironmentDirectory 'scene_parameters.json') -Raw | ConvertFrom-Json
$mapPackages = @($summary.maps | ForEach-Object virtual_package)
$refs = [Collections.Generic.List[object]]::new()
foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.json') {
    $data = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json -AsHashtable
    if (-not $data.Contains('Template')) { continue }
    $path = $data.Template.ObjectPath
    if (-not $path -or -not $path.StartsWith('NZM/Content/')) { continue }
    $package = $path -replace '\.\d+$', ''
    $normalizedFile = $file.FullName.Replace('\', '/')
    $ownMap = @($mapPackages | Where-Object {
        $normalizedFile.Contains(($_ -replace '^/Game/', 'NZM/Content/') + '/')
    })
    if ($ownMap.Count -eq 0) { continue }
    if ($package -ceq ($ownMap[0] -replace '^/Game/', 'NZM/Content/')) { continue }
    $refs.Add([ordered]@{
        source_json = 'Json/' + [IO.Path]::GetRelativePath($root, $file.FullName).Replace('\', '/')
        source_package = $ownMap[0]
        source_class = $data.Type
        source_name = $data.Name
        template_reference = $data.Template
        dependency_package = ($package -replace '^NZM/Content/', '/Game/')
    })
}
$seeds = @($refs | ForEach-Object dependency_package | Sort-Object -Unique)
$output = [IO.Path]::GetFullPath($WorkDirectory)
[IO.Directory]::CreateDirectory($output) | Out-Null
[IO.File]::WriteAllText((Join-Path $output 'template_seeds.json'), ([ordered]@{
    schema_version = 1
    source_path_base = 'PCResources/Environment'
    seed_package_count = $seeds.Count
    reference_count = $refs.Count
    seed_packages = $seeds
    references = @($refs.ToArray())
} | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
[ordered]@{seed_count = $seeds.Count; references = $refs.Count; seeds = $seeds} | ConvertTo-Json -Depth 4
