[CmdletBinding()]
param([string]$EnvironmentDirectory, [string]$WorkDirectory)
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $EnvironmentDirectory) { $EnvironmentDirectory = Join-Path $repositoryRoot 'PCResources/Environment' }
if (-not $WorkDirectory) { $WorkDirectory = Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/PCLightingDependencies' }
$environmentRoot = [IO.Path]::GetFullPath($EnvironmentDirectory)
$savedRoot = [IO.Path]::GetFullPath($WorkDirectory)
$publishRoot = Join-Path $environmentRoot 'Dependencies'
$layers = @(Get-ChildItem -LiteralPath $savedRoot -Directory | Where-Object Name -match '^Layer[1-3]$' | Sort-Object Name)
[IO.Directory]::CreateDirectory((Join-Path $publishRoot 'Json')) | Out-Null
foreach ($layer in $layers) {
    $layerJson = Join-Path $layer.FullName 'Json'
    foreach ($file in Get-ChildItem -LiteralPath $layerJson -File -Recurse) {
        $relative = [IO.Path]::GetRelativePath($layerJson, $file.FullName)
        $target = Join-Path (Join-Path $publishRoot 'Json') $relative
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
        [IO.File]::Copy($file.FullName, $target, $true)
    }
}
$index = Get-Content -LiteralPath (Join-Path $savedRoot 'object_index.json') -Raw | ConvertFrom-Json -AsHashtable
$indexByIdentity = @{}
foreach ($row in $index.objects) { $indexByIdentity[$row.package + '.' + $row.export_index] = $row }
$rawByIdentity = @{}
$rawRows = [Collections.Generic.List[object]]::new()
$rawFailures = [Collections.Generic.List[object]]::new()
foreach ($tree in @('Json', 'Dependencies/Json')) {
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $environmentRoot $tree) -File -Recurse -Filter '*.json') {
        $data = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json -AsHashtable
        $relative = [IO.Path]::GetRelativePath($environmentRoot, $file.FullName).Replace('\', '/')
        $package = $null
        $outerIndex = $null
        $outerPackage = $null
        if ($data.Contains('Package')) { $package = $data.Package }
        elseif ($data.Contains('Outer') -and $data.Outer.ObjectPath -match '^(?<package>.+)\.(?<index>\d+)$') {
            $package = $Matches['package']
            $outerPackage = $package
            $outerIndex = [int]$Matches['index']
        }
        $candidates = @($index.objects | Where-Object {
            $_.package -ceq $package -and $_.name -ceq $data.Name -and $_.class -ceq $data.Type -and
            (($null -eq $outerIndex -and ($null -eq $_.outer -or ($_.outer.export_index -eq -1 -and $_.outer.object_path -ceq $package))) -or
             ($null -ne $outerIndex -and $null -ne $_.outer -and $_.outer.package -ceq $outerPackage -and $_.outer.export_index -eq $outerIndex))
        })
        # The five tables in the parent Json tree were deliberately outside this template index.
        if ($candidates.Count -eq 0 -and $data.Type -eq 'DataTable') { continue }
        if ($candidates.Count -ne 1) {
            $rawFailures.Add([ordered]@{source_json = $relative; matching_index_rows = $candidates.Count; status = 'unresolved; no name-only fallback'})
            continue
        }
        $identity = $candidates[0].package + '.' + $candidates[0].export_index
        if ($rawByIdentity.ContainsKey($identity)) { throw ('Duplicate raw object identity ' + $identity) }
        $item = [pscustomobject]@{identity = $identity; index = $candidates[0]; data = $data; source = $relative; file = $file.FullName}
        $rawByIdentity[$identity] = $item
        $rawRows.Add($item)
    }
}

$topologyNames = @('AttachParent', 'AttachSocketName', 'RelativeLocation', 'RelativeRotation', 'RelativeScale3D', 'bAbsoluteLocation', 'bAbsoluteRotation', 'bAbsoluteScale', 'RootComponent', 'CameraComponent', 'SceneComponent', 'InstanceComponents', 'BlueprintCreatedComponents', 'CreationMethod')
function Is-LocalComponentReference($value, [string]$package) {
    if ($value -is [Collections.IDictionary] -and $value.Contains('ObjectPath')) {
        $reference = [string]$value.ObjectPath
        if ($reference.StartsWith($package + '.', [StringComparison]::Ordinal) -and $indexByIdentity.ContainsKey($reference)) {
            return $indexByIdentity[$reference].class -match 'Component$'
        }
    }
    return $false
}
function Merge-Properties($target, $sources, $incoming, [string]$sourceFile, [string]$prefix = '') {
    foreach ($key in $incoming.Keys) {
        $pointer = $prefix + '/' + $key.Replace('~', '~0').Replace('/', '~1')
        $value = $incoming[$key]
        if ($value -is [Collections.IDictionary] -and -not $value.Contains('ObjectPath')) {
            if (-not $target.Contains($key) -or $target[$key] -isnot [Collections.IDictionary]) {
                $target[$key] = [ordered]@{}
                $sources.Remove($pointer)
            }
            Merge-Properties $target[$key] $sources $value $sourceFile $pointer
        }
        else {
            foreach ($oldPointer in @($sources.Keys | Where-Object { $_.StartsWith($pointer + '/', [StringComparison]::Ordinal) })) { $sources.Remove($oldPointer) }
            $target[$key] = $value
            $sources[$pointer] = $sourceFile
        }
    }
}
function Resolve-Item($item) {
    $chain = [Collections.Generic.List[object]]::new()
    $visited = @{}
    $current = $item
    $terminal = 'native_defaults_not_serialized'
    $unresolved = $null
    while ($null -ne $current) {
        if ($visited.ContainsKey($current.identity)) { $terminal = 'cycle_unresolved'; $unresolved = $current.identity; break }
        $visited[$current.identity] = $true
        $chain.Add($current)
        $template = $current.index.template
        if ($null -eq $template) { break }
        $nextIdentity = $template.package + '.' + $template.export_index
        if (-not $rawByIdentity.ContainsKey($nextIdentity)) {
            $terminal = 'template_object_not_in_exported_closure'
            $unresolved = $template
            break
        }
        $current = $rawByIdentity[$nextIdentity]
    }
    $merged = [ordered]@{}
    $fieldSources = [ordered]@{}
    $omittedTopology = [Collections.Generic.List[object]]::new()
    for ($i = $chain.Count - 1; $i -ge 0; $i--) {
        $sourceItem = $chain[$i]
        $properties = $sourceItem.data['Properties']
        if ($null -eq $properties) { continue }
        $included = [ordered]@{}
        foreach ($name in $properties.Keys) {
            if ($i -gt 0 -and ($name -in $topologyNames -or (Is-LocalComponentReference $properties[$name] $sourceItem.index.package))) {
                $omittedTopology.Add([ordered]@{field = $name; source_json = $sourceItem.source; reason = 'topology and relative transforms remain instance-only; source-template indices/transforms are not transplanted'})
                continue
            }
            $included[$name] = $properties[$name]
        }
        Merge-Properties $merged $fieldSources $included $sourceItem.source
    }
    $templateChain = @($chain | ForEach-Object {
        [ordered]@{
            identity = $_.identity
            object_path = $_.index.object_path
            class = $_.index.class
            source_json = $_.source
            next_template = $_.index.template
            serialized_template_reference = $_.data['Template']
        }
    })
    $kind = if ($item.index.class -match '^(Point|Spot|Directional|Sky)LightComponent$') {'light'} elseif ($item.index.class -eq 'PostProcessVolume') {'postprocess_volume'} elseif ($item.index.class -eq 'CineCameraComponent') {'camera_component'} elseif ($item.index.class -eq 'SceneComponent') {'scene_component'} else {'blueprint_actor_or_component'}
    $missingCore = if ($kind -eq 'light') {@(@('Intensity', 'IntensityUnits', 'LightColor') | Where-Object {-not $merged.Contains($_)})} else {@()}
    [ordered]@{
        instance_identity = $item.identity
        instance_object_path = $item.index.object_path
        package = $item.index.requested_package
        class = $item.index.class
        name = $item.index.name
        category = $kind
        source_json = $item.source
        effective_serialized_properties = $merged
        field_sources = $fieldSources
        template_chain = $templateChain
        template_hops_resolved = $chain.Count - 1
        template_terminal_status = $terminal
        unresolved_template = $unresolved
        inherited_topology_omitted = @($omittedTopology.ToArray())
        missing_serialized_core_fields_after_template_merge = $missingCore
        missing_native_defaults = $true
        construction_script_and_runtime_overrides_evaluated = $false
        world_transform_status = 'unknown; only instance topology and relative transforms retained; actor/root defaults and runtime attachment are not evaluated'
    }
}
$componentRows = @(foreach ($item in $rawRows) {
    if (-not $item.index.requested_package.StartsWith('/Game/Maps/')) { continue }
    if ($item.index.class -match '^(Point|Spot|Directional|Sky)LightComponent$|^PostProcessVolume$|^CineCameraComponent$|^SceneComponent$' -or
        ($null -ne $item.index.template -and $item.index.template.package -cne $item.index.package)) {
        Resolve-Item $item
    }
})
$referenceChecks = @(foreach ($item in $rawRows) {
    $serialized = $item.data['Template']
    if ($null -eq $serialized) { continue }
    $actual = $item.index.template
    $expectedIdentity = [string]$serialized.ObjectPath
    $actualIdentity = $(if ($null -ne $actual) {$actual.package + '.' + $actual.export_index} else {$null})
    [ordered]@{
        source_json = $item.source
        serialized_reference = $serialized
        cue_resolved_template = $actual
        reference_identity_matches = $expectedIdentity -ceq $actualIdentity
        raw_template_object_available = $null -ne $actualIdentity -and $rawByIdentity.ContainsKey($actualIdentity)
    }
})
$proof = [ordered]@{
    schema_version = 1
    identity_method = $index.method
    matched_raw_objects = $rawRows.Count
    unresolved_raw_object_count = $rawFailures.Count
    unresolved_raw_objects = @($rawFailures.ToArray())
    template_reference_check_count = $referenceChecks.Count
    template_identity_mismatch_count = @($referenceChecks | Where-Object {-not $_.reference_identity_matches}).Count
    raw_template_object_unavailable_count = @($referenceChecks | Where-Object {-not $_.raw_template_object_available}).Count
    template_reference_checks = $referenceChecks
    objects = @($rawRows | ForEach-Object {[ordered]@{identity = $_.identity; object_path = $_.index.object_path; class = $_.index.class; source_json = $_.source; exact_outer_identity = $_.index.outer; cue_template = $_.index.template; cue_class = $_.index.class_reference}})
}
[IO.File]::WriteAllText((Join-Path $publishRoot 'object_index.json'), ($proof | ConvertTo-Json -Depth 15), [Text.UTF8Encoding]::new($false))
$resolved = [ordered]@{
    schema_version = 1
    status = 'serialized_template_overlay_only; not final effective runtime lighting'
    source_path_base = 'PCResources/Environment'
    source_scope = 'PC Content/Paks base archives only; Saved patches excluded; no live game or anticheat access'
    method = 'Exact CUE export index and Outer identity associate raw JSON. Follow UObject.Template ResolvedObject chains; ancestor serialized Properties are overlaid by descendant and map-instance fields. False/zero values are preserved. Native defaults are unknown.'
    topology_policy = 'AttachParent, relative transforms, root/component references and creation topology are retained only when explicitly serialized by the map instance. Inherited source-template topology is listed as omitted and is not transplanted.'
    limitations = @('effective_serialized_properties names only the recovered serialized overlay; construction scripts, runtime setters, native defaults, visibility and final renderer output remain unresolved.', 'No stock UE5 value conversion, world-transform computation, material/particle behavior restoration or visual parity is claimed.', 'A missing serialized native LightColor/Intensity/IntensityUnits is unknown. Parent CDO chains are preserved in dependency exports; only an actual UObject.Template chain is overlaid here, not an inferred class-name default.')
    summary = [ordered]@{
        components = $componentRows.Count
        lights = @($componentRows | Where-Object category -eq 'light').Count
        postprocess_volumes = @($componentRows | Where-Object category -eq 'postprocess_volume').Count
        camera_components = @($componentRows | Where-Object category -eq 'camera_component').Count
        scene_components = @($componentRows | Where-Object category -eq 'scene_component').Count
        with_resolved_template_hops = @($componentRows | Where-Object template_hops_resolved -gt 0).Count
        unresolved_template_terminals = @($componentRows | Where-Object {$_.template_terminal_status -ne 'native_defaults_not_serialized'}).Count
        exact_template_reference_checks = $referenceChecks.Count
        reference_identity_mismatches = $proof.template_identity_mismatch_count
        raw_object_association_failures = $rawFailures.Count
    }
    components = $componentRows
}
[IO.File]::WriteAllText((Join-Path $publishRoot 'resolved_components.json'), ($resolved | ConvertTo-Json -Depth 45), [Text.UTF8Encoding]::new($false))
$manifestRows = [Collections.Generic.List[object]]::new()
$packageRows = [Collections.Generic.List[object]]::new()
foreach ($layer in $layers) {
    $layerReport = Get-Content -LiteralPath (Join-Path $layer.FullName 'export_report.json') -Raw | ConvertFrom-Json -AsHashtable
    foreach ($group in @($layerReport.records | Group-Object requested_path)) {
        $sources = @{}
        foreach ($record in $group.Group) {
            foreach ($source in $record.selected_package_files.Values) {
                $sources[$source.virtual_path] = [ordered]@{virtual_path = $source.virtual_path; archive = 'Content/Paks/' + [IO.Path]::GetFileName($source.archive); size = $source.size; compression = $source.compression; archive_offset = $source.archive_offset; provider_read_order = $source.provider_read_order}
            }
        }
        $packageRows.Add([ordered]@{package = $group.Name; dependency_layer = [int]$layer.Name.Substring(5); successful_exports = $group.Count; base_archive_sources = @($sources.Values)})
    }
}
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $publishRoot 'Json') -File -Recurse -Filter '*.json') {
    $relative = [IO.Path]::GetRelativePath($environmentRoot, $file.FullName).Replace('\', '/')
    $item = @($rawRows | Where-Object source -ceq $relative)
    if ($item.Count -ne 1) { throw ('Manifest object association failed: ' + $relative) }
    $manifestRows.Add([ordered]@{file = $relative; identity = $item[0].identity; class = $item[0].index.class; sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()})
}
$manifest = [ordered]@{schema_version = 1; source_path_base = 'PCResources/Environment'; scope = 'Explicit Template, ComponentTemplate, Super, SuperStruct, and ClassDefaultObject dependencies from the exported maps'; package_count = $packageRows.Count; layer_count = $layers.Count; source_scope = 'Base Content/Paks only; no Saved patch priority asserted'; files = @($manifestRows.ToArray()); packages = @($packageRows.ToArray()); host_specific_export_reports_excluded = $true}
[IO.File]::WriteAllText((Join-Path $publishRoot 'export_manifest.json'), ($manifest | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
foreach ($file in @(Get-Item -LiteralPath (Join-Path $savedRoot 'template_seeds.json')) + @(Get-ChildItem -LiteralPath $savedRoot -File -Filter 'closure_after_layer*.json')) { [IO.File]::Copy($file.FullName, (Join-Path $publishRoot $file.Name), $true) }
$resolved.summary | ConvertTo-Json
