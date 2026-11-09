[CmdletBinding()]
param([string]$ExtractionDirectory,[string]$PublishDirectory)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$inputRoot=if($ExtractionDirectory){[IO.Path]::GetFullPath($ExtractionDirectory)}else{Join-Path $repo 'Saved/PCAssetAnalysis/PCShadingDependencies'}
$outputRoot=if($PublishDirectory){[IO.Path]::GetFullPath($PublishDirectory)}else{Join-Path $repo 'PCResources/Shading'}
if(-not$outputRoot.StartsWith($repo+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'PublishDirectory must remain inside this repository.'}
[IO.Directory]::CreateDirectory($outputRoot)|Out-Null
$objects=[Collections.Generic.List[object]]::new()
$packageRecords=[Collections.Generic.List[object]]::new()
$publishedFiles=[Collections.Generic.List[object]]::new()

function Relative-Repo([string]$Path){[IO.Path]::GetRelativePath($repo,$Path).Replace('\','/')}
function Source-Package([string]$Reference){
    if(-not $Reference){return $null}
    $package=$Reference-replace'\.[0-9]+$',''
    if($package.StartsWith('NZM/Content/')){return $package.Replace('NZM/Content/','/Game/')}
    if($package.StartsWith('Engine/Content/')){return $package.Replace('Engine/Content/','/Engine/')}
    return $package
}
function Read-Property($Object,[string]$Name){
    if($null-eq$Object){return $null}
    $property=$Object.PSObject.Properties[$Name]
    if($null-ne$property){return $property.Value}
    return $null
}
$batches=if(Test-Path -LiteralPath (Join-Path $inputRoot 'Export/export_report.json')){@('Export')}else{@('PrimaryParents','SharedFunctions','ProfilesTextures')}
foreach($batch in $batches){
    $report=Get-Content -LiteralPath (Join-Path $inputRoot "$batch/export_report.json") -Raw|ConvertFrom-Json
    if(@($report.records|Where-Object status -ne 'loaded').Count-ne0 -or @($report.exports|Where-Object success -ne $true).Count-ne0){throw "Failed extraction in $batch"}
    foreach($export in $report.exports){
        foreach($file in $export.files){
            $assetPath=$file.Replace('\','/')-replace'^.*?/(Json|Textures)/NZM/Content/',''
            if($assetPath-eq$file.Replace('\','/')){throw 'Unexpected output topology.'}
            $kind=if($export.kind-eq'Json'){'Metadata'}else{'Textures'}
            $destination=Join-Path $outputRoot "Dependencies/$kind/$assetPath"
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))|Out-Null
            if($kind-eq'Metadata'){
                $json=[IO.File]::ReadAllText($file)
                if($json-match'[A-Za-z]:\\'){throw 'Caller-specific filesystem path in asset JSON.'}
                $obj=$json|ConvertFrom-Json
                $exportPackage=Source-Package ($export.object_path.Split('.')[0])
                $record=$report.records|Where-Object{$_.requested_path-eq$exportPackage-and$_.name-eq$obj.Name}|Select-Object -First 1
                [IO.File]::WriteAllText($destination,$json,[Text.UTF8Encoding]::new($false))
                $objects.Add([pscustomobject]@{object=$obj;record=$record;metadata_file=(Relative-Repo $destination);package=$exportPackage})
            }else{[IO.File]::Copy($file,$destination,$true)}
            $publishedFiles.Add([ordered]@{path=(Relative-Repo $destination);bytes=(Get-Item -LiteralPath $destination).Length;sha256=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()})
        }
    }
    foreach($group in $report.records|Group-Object requested_path){
        $top=$group.Group|Where-Object{$_.class-in@('Material','MaterialInstanceConstant','MaterialFunction','SubsurfaceProfile','Texture2D','TextureCube')}|Select-Object -First 1
        $sourceFiles=[ordered]@{}
        foreach($property in $top.selected_package_files.PSObject.Properties){
            $source=$property.Value
            $relativeArchive=$source.archive.Replace('\','/')-replace'^.*?/NZM/',''
            $sourceFiles[$property.Name]=[ordered]@{virtual_path=$source.virtual_path;archive=$relativeArchive;provider_read_order=$source.provider_read_order;size=$source.size;compression=$source.compression;archive_offset=$source.archive_offset}
        }
        $packageRecords.Add([ordered]@{virtual_package=$group.Name;type=$top.class;exported_object_count=$group.Count;selected_package_files=$sourceFiles;texture=$(Read-Property $top 'texture')})
    }
}

$packageLookup=@{}
foreach($record in $packageRecords){$packageLookup[$record.virtual_package]=$record}
$parentEvidence=[Collections.Generic.List[object]]::new()
$functionEvidence=[Collections.Generic.List[object]]::new()
$profileEvidence=[Collections.Generic.List[object]]::new()
$dependencyRefs=[Collections.Generic.List[object]]::new()

foreach($entry in $objects){
    $obj=$entry.object;$props=$obj.Properties
    foreach($match in [regex]::Matches(($obj|ConvertTo-Json -Depth 100),'"ObjectName":\s*"([^'']+)''([^'']+)''",\s*"ObjectPath":\s*"([^"]+)"')){
        $dependencyPackage=Source-Package $match.Groups[3].Value
        if($dependencyPackage-ne$entry.package){
            $dependencyRefs.Add([ordered]@{owner_package=$entry.package;owner_object=$obj.Name;referenced_type=$match.Groups[1].Value;virtual_package=$dependencyPackage;exported_in_this_scope=$packageLookup.ContainsKey($dependencyPackage);evidence=$entry.metadata_file})
        }
    }
    if($obj.Type-eq'Material'){
        $samePackage=@($objects|Where-Object package -eq $entry.package)
        $rawExpressionRefs=Read-Property $props 'Expressions'
        $declaredExpressionCount=if($null-ne$rawExpressionRefs){$rawExpressionRefs.Count}else{0}
        $expressionRefs=@($rawExpressionRefs|Where-Object{$null-ne$_})
        $resolvedRefs=@($expressionRefs|Where-Object{$null-ne$_ -and $null-ne(Read-Property $_ 'ObjectPath')})
        $expressionObjects=@($samePackage|Where-Object{$_.object.Type-like'MaterialExpression*'})
        $attribute=Read-Property $props 'MaterialAttributes'
        $parentEvidence.Add([ordered]@{
            virtual_package=$entry.package;type=$obj.Type;metadata_file=$entry.metadata_file
            shading_model=(Read-Property $props 'ShadingModel');blend_mode=(Read-Property $props 'BlendMode');two_sided=(Read-Property $props 'TwoSided')
            declared_expression_entries=$declaredExpressionCount;non_null_expression_references=$resolvedRefs.Count;exported_expression_objects=$expressionObjects.Count
            material_attributes_expression=(Read-Property $attribute 'Expression');material_attributes_expression_name=(Read-Property $attribute 'ExpressionName')
            graph_status='partial_serialized_metadata_not_a_verified_editable_graph'
            missing_graph=[ordered]@{root_expression_object_reference_null=($null-eq(Read-Property $attribute 'Expression'));null_expression_list_entries=($declaredExpressionCount-$resolvedRefs.Count);all_expression_nodes_available=($declaredExpressionCount-eq$expressionObjects.Count);full_graph_connections_verified=$false;custom_engine_shader_code_available=$false}
            subsurface_profile=(Source-Package (Read-Property (Read-Property $props 'SubsurfaceProfile') 'ObjectPath'))
            stock_ue_equivalence='unverified'
        })
    }elseif($obj.Type-eq'MaterialFunction'){
        $rawFunctionExpressionRefs=Read-Property $props 'FunctionExpressions'
        $functionExpressionCount=if($null-ne$rawFunctionExpressionRefs){$rawFunctionExpressionRefs.Count}else{0}
        $functionExpressionRefs=@($rawFunctionExpressionRefs|Where-Object{$null-ne$_})
        $functionEvidence.Add([ordered]@{virtual_package=$entry.package;metadata_file=$entry.metadata_file;declared_expression_entries=$functionExpressionCount;non_null_expression_references=$functionExpressionRefs.Count;exported_expression_objects=@($objects|Where-Object{$_.package-eq$entry.package-and$_.object.Type-like'MaterialExpression*'}).Count;complete_connections_verified=$false;editable_function_reconstruction='unverified'})
    }elseif($obj.Type-eq'SubsurfaceProfile'){
        $profileEvidence.Add([ordered]@{virtual_package=$entry.package;metadata_file=$entry.metadata_file;explicit_settings=(Read-Property $props 'Settings');units_and_ue5_profile_equivalence='unverified; retain source values as evidence rather than assigning current engine defaults'})
    }
}

$families=[ordered]@{}
foreach($category in @('skin','hair','eyes')){
    $sourceVMIs=[Collections.Generic.List[object]]::new()
    foreach($file in Get-ChildItem -LiteralPath (Join-Path $repo 'PCResources/Metadata') -Filter 'VMI_*.json' -File -Recurse){
        $obj=Get-Content -LiteralPath $file.FullName -Raw|ConvertFrom-Json
        $props=$obj.Properties;$parent=Source-Package (Read-Property (Read-Property $props 'Parent') 'ObjectPath')
        $actualCategory=if($parent-match'/NewCharacterMaterial/Skin/'){'skin'}elseif($parent-match'/NewCharacterMaterial/Hair/'){'hair'}elseif($parent-match'/NewCharacterMaterial/EyeMaterials/'){'eyes'}else{$null}
        if($actualCategory-ne$category){continue}
        $textures=@(Read-Property $props 'TextureParameterValues'|Where-Object{$null-ne$_}|ForEach-Object{[ordered]@{parameter=$_.ParameterInfo.Name;value=$_.ParameterValue;source_virtual_package=(Source-Package (Read-Property $_.ParameterValue 'ObjectPath'))}})
        $profile=Source-Package (Read-Property (Read-Property $props 'SubsurfaceProfile') 'ObjectPath')
        $profileStatus=if(-not$profile){'not_explicitly_overridden; inherited_or_engine_default_unknown'}elseif($packageLookup.ContainsKey($profile)){'exported'}else{'referenced_not_exported_in_20_package_scope'}
        $sourceVMIs.Add([ordered]@{
            virtual_package=(Source-Package $obj.Package);metadata_file=(Relative-Repo $file.FullName);type=$obj.Type;parent=$parent;parent_exported=$packageLookup.ContainsKey($parent)
            explicit_parameters=[ordered]@{scalar=@(Read-Property $props 'ScalarParameterValues'|Where-Object{$null-ne$_});vector=@(Read-Property $props 'VectorParameterValues'|Where-Object{$null-ne$_});texture=$textures;static=(Read-Property $props 'StaticParameters');base_property_overrides=(Read-Property $props 'BasePropertyOverrides')}
            profile=[ordered]@{virtual_package=$profile;override_flag=(Read-Property $props 'bOverrideSubsurfaceProfile');status=$profileStatus}
            lut_references=@($textures|Where-Object{$_.parameter-match'(?i)LUT|Preintegrat' -or $_.source_virtual_package-match'(?i)LUT|Preintegrat'})
            numeric_parameter_transfer_equivalence='unverified; parameters require their source graph and engine behavior'
        })
    }
    $parents=@($sourceVMIs.ToArray()|ForEach-Object{$_['parent']}|Sort-Object -Unique)
    $profiles=@($sourceVMIs.ToArray()|ForEach-Object{$_['profile']['virtual_package']}|Where-Object{$_}|Sort-Object -Unique|ForEach-Object{[ordered]@{virtual_package=$_;exported=$packageLookup.ContainsKey($_)}})
    $families[$category]=[ordered]@{
        source_vmi_count=$sourceVMIs.Count;source_vmis=@($sourceVMIs.ToArray());unique_parents=$parents;profile_dependencies=$profiles
        graph_readiness='partial_graph_metadata_only; not directly importable or proven reconstructable'
        lut_status='No explicit LUT/Preintegrated texture reference was observed in inspected VMI and shared-function metadata; this limited scope cannot establish absence in the full shader implementation.'
        stock_ue_equivalence='unverified';shader_code_recovered=$false;ue_compile_verified=$false;visual_match_verified=$false
    }
}
$uniqueDeps=@($dependencyRefs.ToArray()|ForEach-Object{[pscustomobject]$_}|Sort-Object owner_package,owner_object,referenced_type,virtual_package -Unique)
if($packageRecords.Count-gt20){throw 'Extraction exceeds the documented 20-package scope.'}
$scope=[ordered]@{schema_version=1;source='PC Content/Paks base 80 archives; patch precedence was not merged';cue4parse_version='1.2.2.202611';package_budget=20;requested_packages=$packageRecords.Count;exported_json_objects=@($publishedFiles.ToArray()|Where-Object{$_['path'].EndsWith('.json')}).Count;exported_textures=@($publishedFiles.ToArray()|Where-Object{-not$_['path'].EndsWith('.json')}).Count;packages=@($packageRecords.ToArray());files=@($publishedFiles.ToArray());limitations=@('Cooked metadata is evidence, not an editable UE asset or recovered shader source.','No original cooked package, tool dependency, caller path or user key is included here.','Missing property does not establish a value or restore engine defaults.','No UE import, compilation, rendering or device validation was performed.')}
$readiness=[ordered]@{schema_version=1;status='source_evidence_prepared_requires_ue_reconstruction_and_visual_validation';scope='20 selected shared-parent/function/profile/texture packages plus existing character VMI metadata';families=$families;parent_material_evidence=@($parentEvidence.ToArray());shared_function_evidence=@($functionEvidence.ToArray());exported_subsurface_profiles=@($profileEvidence.ToArray());dependency_references=$uniqueDeps;unresolved_dependencies=@($uniqueDeps|Where-Object{-not$_.exported_in_this_scope});implementation_limits=@('MaterialAttributes root references are null where observed, while only ExpressionName strings survive.','Serialized expression lists, surviving parameter nodes and function references do not contain a verified complete graph.','No private NZM shader source, engine patches, LUT equation, Marschner implementation or eye-refraction implementation is claimed recovered.','Source shading model names match UE enums but do not establish equivalence with this project''s UE5.8/Metal/mobile code paths.','SubsurfaceProfile source units, defaults and kernel semantics require verification before reuse.')}
[IO.File]::WriteAllText((Join-Path $outputRoot 'Dependencies/export_manifest.json'),($scope|ConvertTo-Json -Depth 50),[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $outputRoot 'readiness.json'),($readiness|ConvertTo-Json -Depth 80),[Text.UTF8Encoding]::new($false))
[ordered]@{packages=$packageRecords.Count;json_objects=$scope.exported_json_objects;textures=$scope.exported_textures;skin=$families.skin.source_vmi_count;hair=$families.hair.source_vmi_count;eyes=$families.eyes.source_vmi_count;dependency_references=$uniqueDeps.Count;unresolved=@($readiness.unresolved_dependencies).Count}|ConvertTo-Json -Compress
