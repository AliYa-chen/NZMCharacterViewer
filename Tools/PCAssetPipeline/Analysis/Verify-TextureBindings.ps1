[CmdletBinding()]
param([string]$ProjectRoot=(Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))))
$ErrorActionPreference='Stop'
$project=(Get-Item -LiteralPath $ProjectRoot).FullName
$analysis=Join-Path $project 'Saved/PCAssetAnalysis'
$pcJsonRoot=Join-Path $analysis 'PCExports/MaterialBindings/Json'
$comparisonPath=Join-Path $analysis 'texture_upgrade_comparison.json'
$comparison=Get-Content -LiteralPath $comparisonPath -Raw | ConvertFrom-Json
$latin1=[Text.Encoding]::GetEncoding(28591)

function Read-MaterialDependencies([IO.FileInfo]$File){
 $bytes=[IO.File]::ReadAllBytes($File.FullName)
 if($bytes.Length -lt 128 -or [BitConverter]::ToString($bytes,0,4) -ne 'C1-83-2A-9E'){throw 'Not an editable Unreal package.'}
 $legacy=[BitConverter]::ToInt32($bytes,4);$ue4=[BitConverter]::ToInt32($bytes,12);$ue5=[BitConverter]::ToInt32($bytes,16)
 if($legacy -ne -9 -or $ue4 -ne 522 -or $ue5 -ne 1018){throw 'Unsupported editable package version.'}
 $header=[BitConverter]::ToInt32($bytes,44);$custom=[BitConverter]::ToInt32($bytes,48)
 if($custom -lt 0 -or $custom -gt 1024 -or $header -lt 128 -or $header -gt $bytes.Length){throw 'Invalid package summary bounds.'}
 $folder=52+20*$custom
 if($folder+4 -gt $header){throw 'Folder string outside summary.'}
 $folderLength=[BitConverter]::ToInt32($bytes,$folder)
 if($folderLength -le 0 -or $folderLength -gt 65536 -or $folder+4+$folderLength -gt $header -or $bytes[$folder+3+$folderLength] -ne 0){throw 'Unsupported folder FString.'}
 $nameCountAt=$folder+8+$folderLength
 $nameCount=[BitConverter]::ToInt32($bytes,$nameCountAt);$nameOffset=[BitConverter]::ToInt32($bytes,$nameCountAt+4)
 if($nameCount -lt 1 -or $nameCount -gt 65536 -or $nameOffset -lt $nameCountAt+8 -or $nameOffset -ge $header){throw 'Invalid name map.'}
 $names=[Collections.Generic.List[string]]::new();$nameOffsets=[Collections.Generic.List[int]]::new();$cursor=$nameOffset
 for($i=0;$i -lt $nameCount;$i++){
  if($cursor+4 -gt $header){throw 'Truncated name map.'};$nameOffsets.Add($cursor)
  $n=[BitConverter]::ToInt32($bytes,$cursor);$cursor+=4
  if($n -eq 0 -or [Math]::Abs([long]$n) -gt 65536){throw 'Invalid name-map FString.'}
  $byteLength=if($n -lt 0){-2*$n}else{$n};$terminator=if($n -lt 0){2}else{1}
  if($cursor+$byteLength+4 -gt $header){throw 'Name string outside header.'}
  for($j=1;$j -le $terminator;$j++){if($bytes[$cursor+$byteLength-$j] -ne 0){throw 'Name lacks terminator.'}}
  $encoding=if($n -lt 0){[Text.Encoding]::Unicode}else{[Text.Encoding]::UTF8}
  $names.Add($encoding.GetString($bytes,$cursor,$byteLength-$terminator));$cursor+=$byteLength+4
 }
 function FNameAt([int]$At){
  if($At -lt 0 -or $At+8 -gt $bytes.Length){throw 'FName outside package.'}
  $index=[BitConverter]::ToInt32($bytes,$At);$number=[BitConverter]::ToInt32($bytes,$At+4)
  if($index -lt 0 -or $index -ge $names.Count -or $number -lt 0){throw 'FName index/number invalid.'}
  $value=$names[$index];if($number -gt 0){$value+='_'+($number-1)};return $value
 }
 # Audited UE5.8 summary fields: name map, soft-object-path table, localization
 # FString, gatherable text table, export table, and import table.
 $cursor=$nameCountAt+16
 $localizationLength=[BitConverter]::ToInt32($bytes,$cursor)
 if($localizationLength -eq [int]::MinValue -or [Math]::Abs([long]$localizationLength) -gt 65536){throw 'Unsupported localization FString.'}
 $localizationBytes=if($localizationLength -lt 0){-2*$localizationLength}else{$localizationLength}
 $cursor+=4+$localizationBytes+8
 if($cursor+16 -gt $header){throw 'Export/import summary outside header.'}
 $exportCount=[BitConverter]::ToInt32($bytes,$cursor);$exportOffset=[BitConverter]::ToInt32($bytes,$cursor+4)
 $importCount=[BitConverter]::ToInt32($bytes,$cursor+8);$importOffset=[BitConverter]::ToInt32($bytes,$cursor+12)
 if($exportCount -lt 1 -or $exportCount -gt 65536 -or $importCount -lt 1 -or $importCount -gt 65536 -or $importOffset -lt $nameOffset -or $importOffset+40*$importCount -gt $header -or $exportOffset -lt $nameOffset -or $exportOffset+112*$exportCount -gt $header){throw 'Unsupported import/export table layout or bounds.'}
 $imports=[Collections.Generic.List[object]]::new()
 for($i=0;$i -lt $importCount;$i++){
  $p=$importOffset+40*$i
  $imports.Add([pscustomobject]@{index=$i+1;table_offset=$p;class_package=(FNameAt $p);class_name=(FNameAt ($p+8));outer_index=[BitConverter]::ToInt32($bytes,$p+16);object_name=(FNameAt ($p+20));package_name=(FNameAt ($p+28));optional=[BitConverter]::ToInt32($bytes,$p+36)})
 }
 $textureImports=[Collections.Generic.Dictionary[int,object]]::new()
 foreach($import in $imports){
  if($import.class_name -ne 'Texture2D' -or $import.class_package -ne '/Script/Engine'){continue}
  if($import.outer_index -ge 0 -or -$import.outer_index -gt $imports.Count){throw 'Texture import has unsupported outer index.'}
  $outer=$imports[-$import.outer_index-1]
  if($outer.class_name -ne 'Package' -or $outer.outer_index -ne 0 -or $outer.object_name -notlike '/Game/Characters/Textures/*'){continue}
  if([IO.Path]::GetFileName($outer.object_name) -ine $import.object_name){throw 'Texture import object name differs from its package leaf.'}
  $packageNameIndex=[BitConverter]::ToInt32($bytes,$outer.table_offset+20)
  $textureImports.Add(-$import.index,[pscustomobject]@{texture_package=$outer.object_name;texture_object=$import.object_name;texture_import_index=$import.index;texture_import_table_offset=$import.table_offset;package_import_index=$outer.index;package_import_table_offset=$outer.table_offset;package_name_map_index=$packageNameIndex;package_name_map_offset=$nameOffsets[$packageNameIndex];typed_texture_properties=[Collections.Generic.List[object]]::new()})
 }
 $textureNameIndex=$names.IndexOf('Texture');$objectPropertyIndex=$names.IndexOf('ObjectProperty')
 if($textureNameIndex -ge 0 -and $objectPropertyIndex -ge 0){
  $prefix=[byte[]](@([BitConverter]::GetBytes([int]$textureNameIndex))+@(0,0,0,0)+@([BitConverter]::GetBytes([int]$objectPropertyIndex))+@(0,0,0,0,0,0,0,0,4,0,0,0,0))
  $needle=$latin1.GetString($prefix);$binary=$latin1.GetString($bytes)
  for($i=0;$i -lt $exportCount;$i++){
   $p=$exportOffset+112*$i;$classIndex=[BitConverter]::ToInt32($bytes,$p)
   if($classIndex -ge 0 -or -$classIndex -gt $imports.Count){continue}
   $className=$imports[-$classIndex-1].object_name
   if($className -notlike 'MaterialExpressionTextureSample*'){continue}
   $size=[BitConverter]::ToInt64($bytes,$p+28);$start=[BitConverter]::ToInt64($bytes,$p+36)
   if($size -lt 0 -or $start -lt $header -or $start+$size -gt $bytes.Length){throw 'Texture expression export outside package.'}
   $end=[int]($start+$size);$search=[int]$start
   while($search+$prefix.Length+4 -le $end){
    $found=$binary.IndexOf($needle,$search,$end-$search,[StringComparison]::Ordinal)
    if($found -lt 0){break};$valueAt=$found+$prefix.Length
    if($valueAt+4 -gt $end){throw 'Texture property payload outside expression export.'}
    $reference=[BitConverter]::ToInt32($bytes,$valueAt)
    if($textureImports.ContainsKey($reference)){
     $textureImports[$reference].typed_texture_properties.Add([pscustomobject]@{export_index=$i+1;export_class=$className;export_object_name=(FNameAt ($p+16));export_serial_offset=$start;export_serial_size=$size;property_name='Texture';property_type='ObjectProperty';property_tag_offset=$found;property_value_offset=$valueAt;serialized_package_index=$reference})
    }
    $search=$found+1
   }
  }
 }
 $stem=[IO.Path]::GetFileNameWithoutExtension($File.Name);$originalName=$null
 if($stem -match '^M_(.+)_[a-fA-F0-9]{8}$'){$originalName=$Matches[1]}
 return [pscustomobject]@{file=[IO.Path]::GetRelativePath($project,$File.FullName).Replace('\','/');sha256=(Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash;status='parsed';original_material_name=$originalName;package_versions=[pscustomobject]@{legacy=$legacy;ue4=$ue4;ue5=$ue5};header_size=$header;name_count=$nameCount;name_offset=$nameOffset;import_count=$importCount;import_offset=$importOffset;export_count=$exportCount;export_offset=$exportOffset;texture_dependencies=@($textureImports.Values)}
}

$materials=[Collections.Generic.List[object]]::new();$materialFailures=[Collections.Generic.List[object]]::new()
$files=@(Get-ChildItem -LiteralPath (Join-Path $project 'Content/Characters') -Recurse -Filter '*.uasset' -File | Where-Object DirectoryName -like '*\Materials' | Sort-Object FullName)
foreach($file in $files){try{$materials.Add((Read-MaterialDependencies $file))}catch{$materialFailures.Add([pscustomobject]@{file=[IO.Path]::GetRelativePath($project,$file.FullName).Replace('\','/');error=$_.Exception.Message})}}
$consumersByTexture=[Collections.Generic.Dictionary[string,Collections.Generic.List[object]]]::new([StringComparer]::OrdinalIgnoreCase)
foreach($material in $materials){foreach($dependency in $material.texture_dependencies){if(-not $consumersByTexture.ContainsKey($dependency.texture_package)){$consumersByTexture.Add($dependency.texture_package,[Collections.Generic.List[object]]::new())};$consumersByTexture[$dependency.texture_package].Add([pscustomobject]@{material=$material;dependency=$dependency})}}
$pcByName=[Collections.Generic.Dictionary[string,Collections.Generic.List[object]]]::new([StringComparer]::OrdinalIgnoreCase)
$pcFiles=@(Get-ChildItem -LiteralPath $pcJsonRoot -Recurse -Filter '*.json' -File | Sort-Object FullName)
foreach($file in $pcFiles){
 $objects=@(Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json)
 foreach($object in $objects){
  $parameters=@(foreach($parameter in @($object.Properties.TextureParameterValues)){
   if($null -eq $parameter){continue};$objectPath=[string]$parameter.ParameterValue.ObjectPath
   $sourcePackage=$objectPath -replace '\.[^./]+$',''
   $sourcePackage=$sourcePackage -replace '^NZM/Content/','/Game/' -replace '^Engine/Content/','/Engine/'
   [pscustomobject]@{parameter_name=$parameter.ParameterInfo.Name;parameter_association=$parameter.ParameterInfo.Association;parameter_index=$parameter.ParameterInfo.Index;object_name=$parameter.ParameterValue.ObjectName;object_path=$objectPath;source_package=$sourcePackage}
  })
  $row=[pscustomobject]@{name=$object.Name;type=$object.Type;package=$object.Package;json_file=[IO.Path]::GetRelativePath($project,$file.FullName).Replace('\','/');json_sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash;parent=$object.Properties.Parent;texture_parameters=$parameters}
  if(-not $pcByName.ContainsKey($object.Name)){$pcByName.Add($object.Name,[Collections.Generic.List[object]]::new())};$pcByName[$object.Name].Add($row)
 }
}
$targets=@($comparison.assets | Where-Object comparison -eq 'actualhigher')
if($targets.Count -ne 100){throw 'Expected the established 100 actualhigher comparison rows.'}
$bindings=[Collections.Generic.List[object]]::new()
foreach($target in $targets){
 $currentPackage=([IO.Path]::ChangeExtension($target.current_file,$null).TrimEnd('.') -replace '^Content/','/Game/').Replace('\','/')
 $expected=$target.proposed_source.requested_path
 $consumers=[Collections.Generic.List[object]]::new();$conflicts=[Collections.Generic.List[object]]::new()
 if($consumersByTexture.ContainsKey($currentPackage)){
  foreach($consumer in $consumersByTexture[$currentPackage]){
   $material=$consumer.material;$dependency=$consumer.dependency;$pcMatches=@()
   if($null -ne $material.original_material_name -and $pcByName.ContainsKey($material.original_material_name)){$pcMatches=@($pcByName[$material.original_material_name])}
   $pcEvidence=@(foreach($pc in $pcMatches){
    $matching=@($pc.texture_parameters | Where-Object source_package -ieq $expected)
    [pscustomobject]@{name=$pc.name;type=$pc.type;package=$pc.package;json_file=$pc.json_file;json_sha256=$pc.json_sha256;parent=$pc.parent;matching_proposed_parameters=$matching;all_texture_parameters=$pc.texture_parameters}
   })
   $reasons=[Collections.Generic.List[string]]::new()
   if($dependency.typed_texture_properties.Count -eq 0){$reasons.Add('Current Texture2D import lacks an explicit Texture:ObjectProperty reference in a MaterialExpressionTextureSample export.')}
   if($pcMatches.Count -eq 0){$reasons.Add('No PC material JSON has the recovered original material name.')}
   if($pcMatches.Count -gt 1){$reasons.Add('Multiple PC full material paths share this original material name.')}
   if($pcMatches.Count -eq 1 -and $pcEvidence[0].matching_proposed_parameters.Count -eq 0){$reasons.Add('The same-name PC material has no TextureParameterValues reference to the proposed source package.')}
   $verified=$reasons.Count -eq 0
   $consumers.Add([pscustomobject]@{current_material_file=$material.file;current_material_sha256=$material.sha256;original_material_name=$material.original_material_name;current_dependency=$dependency;pc_material_candidate_count=$pcMatches.Count;pc_materials=$pcEvidence;consumer_binding_verified=$verified;failure_reasons=@($reasons)})
  }
 }
 $knownParameters=@($consumers | ForEach-Object pc_materials | ForEach-Object matching_proposed_parameters | ForEach-Object parameter_name | Sort-Object -Unique)
 foreach($consumer in $consumers){
  if($consumer.consumer_binding_verified){continue}
  $differentSources=@($consumer.pc_materials | ForEach-Object all_texture_parameters | Where-Object {$_.parameter_name -in $knownParameters -and $_.source_package -ine $expected})
  $conflicts.Add([pscustomobject]@{current_material_file=$consumer.current_material_file;reasons=$consumer.failure_reasons;observed_same_parameter_names_with_other_sources=$differentSources;pc_materials=@($consumer.pc_materials | Select-Object name,package,json_file,all_texture_parameters)})
 }
 $verified=$consumers.Count -gt 0 -and @($consumers | Where-Object {-not $_.consumer_binding_verified}).Count -eq 0 -and $materialFailures.Count -eq 0
 $status=if($verified){'binding_verified'}elseif($consumers.Count -eq 0){'no_current_consumer_found'}elseif($materialFailures.Count -gt 0){'incomplete_current_material_coverage'}else{'consumer_binding_unverified_or_conflicting'}
 $bindings.Add([pscustomobject]@{current_texture_file=$target.current_file;current_texture_package=$currentPackage;normalized_source_name=$target.normalized_source_name;current_source_width=$target.current_source_width;current_source_height=$target.current_source_height;proposed_source=$target.proposed_source;binding_verified=$verified;status=$status;consumer_count=$consumers.Count;verified_consumer_count=@($consumers | Where-Object consumer_binding_verified).Count;pc_parameter_names=$knownParameters;consumers=@($consumers);conflicts=@($conflicts);uv_equivalence_verified=$false;visual_result_verified=$false;project_texture_replaced=$false})
}
$summary=[ordered]@{actualhigher_targets=$bindings.Count;binding_verified=@($bindings | Where-Object binding_verified).Count;binding_unverified=@($bindings | Where-Object {-not $_.binding_verified}).Count;targets_without_consumers=@($bindings | Where-Object consumer_count -eq 0).Count;current_material_files_discovered=$files.Count;current_material_files_parsed=$materials.Count;current_material_parse_failures=$materialFailures.Count;pc_material_json_files=$pcFiles.Count;consumer_edges=@($bindings | ForEach-Object consumers).Count;verified_consumer_edges=@($bindings | ForEach-Object consumers | Where-Object consumer_binding_verified).Count}
$invalidVerified=[Collections.Generic.List[string]]::new()
foreach($binding in @($bindings | Where-Object binding_verified)){
 if($binding.consumer_count -lt 1){$invalidVerified.Add($binding.current_texture_file);continue}
 foreach($consumer in $binding.consumers){
  if(-not $consumer.consumer_binding_verified -or $consumer.current_dependency.texture_package -ine $binding.current_texture_package -or $consumer.current_dependency.typed_texture_properties.Count -lt 1 -or $consumer.pc_material_candidate_count -ne 1 -or $consumer.pc_materials[0].matching_proposed_parameters.Count -lt 1){$invalidVerified.Add($binding.current_texture_file);continue}
  foreach($property in $consumer.current_dependency.typed_texture_properties){if($property.serialized_package_index -ne -$consumer.current_dependency.texture_import_index){$invalidVerified.Add($binding.current_texture_file)}}
  foreach($parameter in $consumer.pc_materials[0].matching_proposed_parameters){if($parameter.source_package -ine $binding.proposed_source.requested_path){$invalidVerified.Add($binding.current_texture_file)}}
 }
}
if($invalidVerified.Count -gt 0){throw 'A binding marked verified failed its evidence-chain invariant.'}
$materialChanges=@($materials | Where-Object {(Get-FileHash -LiteralPath (Join-Path $project $_.file) -Algorithm SHA256).Hash -ne $_.sha256})
if($materialChanges.Count -gt 0){throw 'Current material files changed while the evidence report was built.'}
$validation=[ordered]@{verified_chain_invariant_failures=$invalidVerified.Count;current_material_hashes_rechecked=$materials.Count;current_material_files_changed_during_run=$materialChanges.Count;all_100_targets_covered=($bindings.Count -eq 100);current_material_parse_complete=($materialFailures.Count -eq 0 -and $materials.Count -eq $files.Count)}
$report=[ordered]@{schema_version=1;generated_at_utc=[DateTime]::UtcNow.ToString('o');method='UE5.8 current material name maps, 40-byte import maps, and 112-byte export maps resolve Texture2D outer-package imports. A complete Texture:ObjectProperty tag inside a MaterialExpressionTextureSample export must reference that import. The original material filename name must identify a unique PC material JSON whose TextureParameterValues explicitly reference the proposed PC full source package. Every current consumer must pass.';scope='Only the 100 actualhigher proposals from texture_upgrade_comparison.json. Offline project/exported JSON reads only.';limitations=@('binding_verified proves the stated dependency/same-name material/explicit PC parameter chain. It does not establish UV equivalence, exact graph-output wiring, visual fidelity, or completed UE replacement.','Current original material names are recovered from M_<name>_<8hex> filenames. The importer hash is retained as provenance, not inverted into a source path.','Multiple PC material full paths with one name remain ambiguous. A failing consumer prevents verification of the entire shared texture target.','PC material JSON and Texture2D measurements cover the stated base Pak provider selection. Live update priority remains unverified.','Only the audited UE5.8 editable material layout is parsed. Any failed material prevents complete-consumer verification.');summary=$summary;validation=$validation;source_comparison=[pscustomobject]@{file=[IO.Path]::GetRelativePath($project,$comparisonPath).Replace('\','/');sha256=(Get-FileHash -LiteralPath $comparisonPath -Algorithm SHA256).Hash};material_parse_failures=@($materialFailures);current_materials=@($materials);bindings=@($bindings)}
$output=Join-Path $analysis 'verified_texture_bindings.json'
[IO.File]::WriteAllText($output,($report | ConvertTo-Json -Depth 24 -Compress),[Text.UTF8Encoding]::new($false))
$summary | ConvertTo-Json -Compress
Write-Output ('Report: '+$output)
