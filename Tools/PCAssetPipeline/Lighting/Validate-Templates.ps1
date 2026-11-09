[CmdletBinding()]
param([string]$EnvironmentDirectory)
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if (-not $EnvironmentDirectory) { $EnvironmentDirectory = Join-Path $repositoryRoot 'PCResources/Environment' }
$environmentRoot = [IO.Path]::GetFullPath($EnvironmentDirectory)
$root = Join-Path $environmentRoot 'Dependencies'
$report = Get-Content -LiteralPath (Join-Path $root 'resolved_components.json') -Raw | ConvertFrom-Json -AsHashtable
$proof = Get-Content -LiteralPath (Join-Path $root 'object_index.json') -Raw | ConvertFrom-Json -AsHashtable
$manifest = Get-Content -LiteralPath (Join-Path $root 'export_manifest.json') -Raw | ConvertFrom-Json -AsHashtable
$documents = @{}
$fieldCount = 0
$inheritedFields = 0
$falseFields = 0
$topologyChecks = 0
function Read-Source($relative) {
    $absolute = [IO.Path]::GetFullPath((Join-Path $environmentRoot $relative))
    if (-not $absolute.StartsWith($environmentRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Source path escapes EnvironmentDirectory.' }
    if (-not $documents.ContainsKey($relative)) { $documents[$relative] = Get-Content -LiteralPath $absolute -Raw | ConvertFrom-Json -AsHashtable }
    return $documents[$relative]
}
function Pointer-Value($value, $pointer) {
    $current = $value
    foreach ($escaped in $pointer.Substring(1).Split('/')) {
        $key = $escaped.Replace('~1', '/').Replace('~0', '~')
        if ($current -isnot [Collections.IDictionary] -or -not $current.Contains($key)) { return [pscustomobject]@{exists=$false;value=$null} }
        $current = $current[$key]
    }
    return [pscustomobject]@{exists=$true;value=$current}
}
foreach ($component in $report.components) {
    foreach ($pointer in $component.field_sources.Keys) {
        $source = Read-Source $component.field_sources[$pointer]
        $sourceValue = Pointer-Value $source['Properties'] $pointer
        $mergedValue = Pointer-Value $component.effective_serialized_properties $pointer
        if (-not $sourceValue.exists -or -not $mergedValue.exists) { throw ('Unresolved source pointer ' + $component.instance_identity + $pointer) }
        if (($sourceValue.value | ConvertTo-Json -Depth 60 -Compress) -cne ($mergedValue.value | ConvertTo-Json -Depth 60 -Compress)) { throw ('Source value differs from merged field ' + $component.instance_identity + $pointer) }
        $fieldCount++
        if ($component.field_sources[$pointer] -cne $component.source_json) { $inheritedFields++ }
        if ($mergedValue.value -is [bool] -and -not $mergedValue.value) { $falseFields++ }
    }
    $instance = Read-Source $component.source_json
    foreach ($name in @('AttachParent', 'AttachSocketName', 'RelativeLocation', 'RelativeRotation', 'RelativeScale3D', 'bAbsoluteLocation', 'bAbsoluteRotation', 'bAbsoluteScale', 'RootComponent', 'CameraComponent', 'SceneComponent', 'InstanceComponents', 'BlueprintCreatedComponents', 'CreationMethod')) {
        $presentOnInstance = $null -ne $instance['Properties'] -and $instance.Properties.Contains($name)
        if ($component.effective_serialized_properties.Contains($name) -ne $presentOnInstance) { throw ('Inherited topology unexpectedly inserted: ' + $component.instance_identity + '/' + $name) }
        if ($presentOnInstance -and (($instance.Properties[$name] | ConvertTo-Json -Depth 60 -Compress) -cne ($component.effective_serialized_properties[$name] | ConvertTo-Json -Depth 60 -Compress))) { throw ('Instance topology changed: ' + $component.instance_identity + '/' + $name) }
        $topologyChecks++
    }
}
foreach ($file in $manifest.files) {
    $path = [IO.Path]::GetFullPath((Join-Path $environmentRoot $file.file))
    if (-not $path.StartsWith($environmentRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path escapes EnvironmentDirectory.' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $file.sha256) { throw ('Dependency JSON hash mismatch: ' + $file.file) }
}
if ($proof.template_identity_mismatch_count -ne 0 -or $proof.unresolved_raw_object_count -ne 0 -or $proof.raw_template_object_unavailable_count -ne 0) { throw 'Exact template identity checks are incomplete; inspect object_index.json.' }
[ordered]@{
    status = 'passed'
    checked_components = $report.components.Count
    checked_field_sources = $fieldCount
    inherited_field_values_checked = $inheritedFields
    explicit_false_field_values_preserved = $falseFields
    instance_topology_checks = $topologyChecks
    dependency_json_hashes_checked = $manifest.files.Count
    exact_template_references_checked = $proof.template_reference_check_count
    runtime_or_visual_validation = 'not_performed'
} | ConvertTo-Json
