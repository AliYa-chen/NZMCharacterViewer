[CmdletBinding()]
param([ValidateRange(1, 3)][int]$Layer = 1, [string]$WorkDirectory)
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $WorkDirectory) { $WorkDirectory = Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/PCLightingDependencies' }
$root = [IO.Path]::GetFullPath($WorkDirectory)
$seeds = Get-Content -LiteralPath (Join-Path $root 'template_seeds.json') -Raw | ConvertFrom-Json
$known = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($seed in $seeds.seed_packages) { [void]$known.Add($seed) }
for ($i = 1; $i -lt $Layer; $i++) {
    $prior = Get-Content -LiteralPath (Join-Path $root ('closure_after_layer' + $i + '.json')) -Raw | ConvertFrom-Json
    foreach ($path in $prior.next_packages) { [void]$known.Add($path) }
}
$refs = [Collections.Generic.List[object]]::new()
function Find-ChainReferences($value, [string]$pointer, [string]$source) {
    if ($value -is [Collections.IDictionary]) {
        foreach ($key in $value.Keys) {
            $child = $value[$key]
            $childPointer = $pointer + '/' + $key
            if ($key -match '^(Template|Super|SuperStruct|ClassDefaultObject|ParentClass|ComponentTemplate)$' -and $child -is [Collections.IDictionary] -and $child.Contains('ObjectPath')) {
                $path = [string]$child.ObjectPath
                $package = $path -replace '\.\d+$', ''
                if ($package.StartsWith('NZM/Content/')) { $package = $package -replace '^NZM/Content/', '/Game/' }
                $refs.Add([ordered]@{
                    source_json = $source
                    pointer = $childPointer
                    reference = $child
                    dependency_package = $package
                    is_native_reference = $path.StartsWith('/Script/')
                })
            }
            Find-ChainReferences $child $childPointer $source
        }
    }
    elseif ($value -is [Collections.IList]) {
        for ($i = 0; $i -lt $value.Count; $i++) { Find-ChainReferences $value[$i] ($pointer + '/' + $i) $source }
    }
}
$layerRoot = Join-Path $root ('Layer' + $Layer)
foreach ($file in @(Get-ChildItem -LiteralPath (Join-Path $layerRoot 'Json') -File -Recurse)) {
    $data = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json -AsHashtable
    Find-ChainReferences $data '' ('Dependencies/' + [IO.Path]::GetRelativePath($layerRoot, $file.FullName).Replace('\', '/'))
}
$next = @($refs | Where-Object { -not $_.is_native_reference -and $_.dependency_package.StartsWith('/Game/') -and -not $known.Contains($_.dependency_package) } | ForEach-Object dependency_package | Sort-Object -Unique)
$report = [ordered]@{
    schema_version = 1
    source_path_base = 'PCResources/Environment'
    completed_layer = $Layer
    previously_requested_package_count = $known.Count
    next_package_count = $next.Count
    next_packages = $next
    native_references_status = 'Recorded only; native class defaults are not guessed from this JSON batch.'
    references = @($refs.ToArray())
}
[IO.File]::WriteAllText((Join-Path $root ('closure_after_layer' + $Layer + '.json')), ($report | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
[ordered]@{layer = $Layer; reference_count = $refs.Count; next_package_count = $next.Count; next_packages = $next} | ConvertTo-Json -Depth 5
