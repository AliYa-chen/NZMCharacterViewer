<#
.SYNOPSIS
Collects configured PC character meshes and explicit material/texture dependencies.
.DESCRIPTION
Reads only NZM disk packages using the repository's locked offline runtime.
PlanJson is retained verbatim; a JSON array of mesh paths is also supported.
Existing PCResources files are referenced with hashes instead of copied again.
Animations are recorded as configuration references, not blindly converted.
No process inspection, memory access, debugging, injection, game launch or UI use.
.EXAMPLE
pwsh -File Tools/PCAssetPipeline/Characters/Collect-CharacterResources.ps1 -GameDirectory 'Y:\games\NZM' -MeshPathsFile Saved/mesh_paths.json
#>
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$GameDirectory,
 [string]$PlanJson,
 [string]$MeshPathsFile,
 [string]$OutputDirectory,
 [string]$CacheDirectory,
 [ValidateRange(1,2000)][int]$MaterialBudget=200,
 [ValidateRange(1,128)][int]$MaximumDependencyRounds=24,
 [ValidateRange(1,128)][int]$MeshBatchSize=12,
 [ValidateRange(1,512)][int]$DependencyBatchSize=96,
 [switch]$SkipValidation,
 [switch]$NoExistingReuse,
 [switch]$Resume,
 [switch]$PlanOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repositoryRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if(-not $OutputDirectory){$OutputDirectory=Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/ExpandedCharacters/Resources'}
if(-not $CacheDirectory){$CacheDirectory=Join-Path $repositoryRoot 'Saved/PCAssetAnalysis/ToolRuntime'}
$game=(Get-Item -LiteralPath $GameDirectory).FullName.TrimEnd('\','/')
$output=[IO.Path]::GetFullPath($OutputDirectory)
if($output.Equals($game,[StringComparison]::OrdinalIgnoreCase) -or $output.StartsWith($game+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'OutputDirectory must be outside GameDirectory.'}
if(-not $PlanJson -and -not $MeshPathsFile){throw 'Provide PlanJson or MeshPathsFile.'}
$null=[IO.Directory]::CreateDirectory($output)
$exporter=Join-Path $PSScriptRoot '../Runtime/Export-Assets.ps1'
$validator=Join-Path $PSScriptRoot '../Runtime/Validate-Exports.ps1'
$failures=[Collections.Generic.List[object]]::new()
$steps=[Collections.Generic.List[object]]::new()
$meshEdges=[Collections.Generic.List[object]]::new()
$materialEdges=[Collections.Generic.List[object]]::new()
$deferred=[Collections.Generic.List[object]]::new()
$resources=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
$meshPaths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$texturePaths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$materialQueue=[Collections.Generic.Queue[string]]::new()
$materialSeen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$inputEvidence=[Collections.Generic.List[object]]::new()
$sourcePlan=$null
$report=[ordered]@{}
$previousExports=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
$resumedReports=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)

function Get-Optional($Object,[string]$Name,$Default=$null){
 if($null -eq $Object){return $Default}
 if($Object -is [Collections.IDictionary]){if($Object.Contains($Name)){return $Object[$Name]};return $Default}
 $property=$Object.PSObject.Properties[$Name];if($null -ne $property){return $property.Value};return $Default
}
function ConvertTo-Package([string]$Path){
 if(-not $Path){return $null}
 $value=$Path.Replace('\','/')
 if($value -match "^[^']+'([^']+)'$"){$value=$Matches[1]}
 $value=$value -replace '^(\.\./)+','' -replace '\.(?:uasset|uexp|ubulk|umap|uptnl)$',''
 $value=$value -replace '\.[^./]+$',''
 $value=$value -replace '^NZM/Content/','/Game/' -replace '^Engine/Content/','/Engine/'
 if($value -notmatch '^/(Game|Engine)/' -or $value -match '(^|/)\.\.(/|$)'){return $null}
 return $value
}
function Find-MeshPaths($Value){
 if($null -eq $Value){return}
 if($Value -is [string]){
  $package=ConvertTo-Package $Value
  if($package -and [IO.Path]::GetFileName($package) -match '^(SK_|SkeletalMesh)'-and $package -notmatch '(?i)Skeleton|PhysicsAsset'){$null=$meshPaths.Add($package)}
  return
 }
 if($Value -is [Collections.IDictionary]){foreach($key in $Value.Keys){Find-MeshPaths $Value[$key]};return}
 if($Value -is [Collections.IEnumerable]){foreach($item in $Value){Find-MeshPaths $item};return}
 foreach($property in $Value.PSObject.Properties){Find-MeshPaths $property.Value}
}
function Write-Json([string]$Path,$Value){[IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 60),[Text.UTF8Encoding]::new($false))}
function Add-Failure([string]$Stage,[string]$Path,[string]$Message){$failures.Add([pscustomobject]@{stage=$Stage;source_package=$Path;error=$Message})}
function File-Evidence([string]$File,[string]$Origin){
 [pscustomobject]@{file=[IO.Path]::GetRelativePath($repositoryRoot,$File).Replace('\','/');bytes=(Get-Item -LiteralPath $File).Length;sha256=(Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant();origin=$Origin}
}

# Build exact source-package -> already published metadata/mesh/texture files.
$existingManifestPath=Join-Path $repositoryRoot 'PCResources/source_manifest.json'
$existing=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
if(-not $NoExistingReuse -and (Test-Path -LiteralPath $existingManifestPath)){
 $old=Get-Content -LiteralPath $existingManifestPath -Raw|ConvertFrom-Json
 $published=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
 foreach($file in $old.files){$published[$file.file]=$file}
 foreach($source in $old.source_objects){
  $package=ConvertTo-Package $source.requested_path
  if(-not $package -or $existing.ContainsKey($package)){continue}
  $tail=if($package.StartsWith('/Game/')){$package.Substring(6)}else{'Engine/'+$package.Substring(8)}
  $metadataRelative='Metadata/'+$tail+'.json'
  $candidates=@($metadataRelative,('Meshes/'+$tail+'.psk'),('Meshes/'+$tail+'.pskx'),('Textures/'+$tail+'.png'))
  $available=[Collections.Generic.List[object]]::new()
  foreach($relative in $candidates){
   if(-not $published.ContainsKey($relative)){continue}
   $absolute=Join-Path $repositoryRoot ('PCResources/'+$relative)
   if(-not [IO.File]::Exists($absolute)){continue}
   $hash=(Get-FileHash -LiteralPath $absolute -Algorithm SHA256).Hash.ToLowerInvariant()
   if($hash -ne $published[$relative].sha256){throw ('Existing PCResources checksum mismatch: '+$relative)}
   $available.Add((File-Evidence $absolute 'existing_PCResources'))
  }
  if(@($available|Where-Object file -like '*.json').Count -gt 0){$existing.Add($package,[pscustomobject]@{source_record=$source;files=@($available);metadata_file=(Join-Path $repositoryRoot ('PCResources/'+$metadataRelative))})}
 }
 # Some published material/parent JSON was supplied by dependency batches and
 # is intentionally absent from source_objects. Resolve its serialized Package,
 # preserving every published inline export belonging to that package.
 foreach($publishedFile in $old.files){
  if($publishedFile.file -notmatch '(^|/)Metadata/.+\.json$'){continue}
  $absolute=Join-Path $repositoryRoot ('PCResources/'+$publishedFile.file)
  if(-not [IO.File]::Exists($absolute)){continue}
  $objects=@(Get-Content -LiteralPath $absolute -Raw|ConvertFrom-Json)
  if($objects.Count -eq 0){continue}
  $package=ConvertTo-Package ([string](Get-Optional $objects[0] 'Package' ''))
  if(-not $package){continue}
  $hash=(Get-FileHash -LiteralPath $absolute -Algorithm SHA256).Hash.ToLowerInvariant()
  if($hash -ne $publishedFile.sha256){throw ('Existing PCResources checksum mismatch: '+$publishedFile.file)}
  $evidence=File-Evidence $absolute 'existing_PCResources'
  if($existing.ContainsKey($package)){
   $oldEntry=$existing[$package]
   if(@($oldEntry.files|Where-Object file -ieq $evidence.file).Count -eq 0){$oldEntry.files=@($oldEntry.files)+@($evidence)}
  }else{$existing.Add($package,[pscustomobject]@{source_record=$null;files=@($evidence);metadata_file=$absolute})}
 }
}

foreach($inputPath in @($PlanJson,$MeshPathsFile)){
 if(-not $inputPath){continue}
 $absolute=(Get-Item -LiteralPath $inputPath).FullName
 $inputObject=Get-Content -LiteralPath $absolute -Raw|ConvertFrom-Json
 if($inputPath -eq $PlanJson){
  $sourcePlan=$inputObject
  $explicitPaths=Get-Optional $inputObject 'mesh_paths'
  if($null -eq $explicitPaths){throw 'PlanJson must provide an explicit mesh_paths array; unrelated route fields are not scanned.'}
  foreach($candidate in $explicitPaths){
   $package=ConvertTo-Package ([string]$candidate)
   if(-not $package -or $package -match '(?i)Skeleton|PhysicsAsset'){throw ('Invalid planned mesh package: '+$candidate)}
   $null=$meshPaths.Add($package)
  }
 }else{
  foreach($candidate in @($inputObject)){
   if($candidate -isnot [string]){throw 'MeshPathsFile must be a JSON array of full package path strings.'}
   $package=ConvertTo-Package $candidate
   if(-not $package -or $package -match '(?i)Skeleton|PhysicsAsset'){throw ('Invalid mesh package: '+$candidate)}
   $null=$meshPaths.Add($package)
  }
 }
 $evidence=File-Evidence $absolute 'input_plan'
 $inputEvidence.Add($evidence)
 if($inputPath -eq $PlanJson){
  [IO.File]::Copy($absolute,(Join-Path $output 'source_plan.json'),$true)
  $deferred.Add([pscustomobject]@{kind='animation_skeleton_and_route_configuration';source=$evidence.file;retained_file=[IO.Path]::GetRelativePath($repositoryRoot,(Join-Path $output 'source_plan.json')).Replace('\','/');status='retained_verbatim_not_animation_converted'})
 }
}
if($meshPaths.Count -eq 0){throw 'The supplied plan has no full SK_ mesh package paths.'}
$orderedMeshes=@($meshPaths|Sort-Object)
Write-Json (Join-Path $output 'mesh_paths.json') $orderedMeshes

# Resume refers only to reports from this same output/input tree, never the
# earlier base-only PCResources. Successful packages are indexed independently
# of batch boundaries so an updated available-only route plan can reuse them.
if($Resume -and -not $PlanOnly){
 foreach($file in @(Get-ChildItem -LiteralPath $output -Filter 'export_report.json' -Recurse -File)){
  $previous=Get-Content -LiteralPath $file.FullName -Raw|ConvertFrom-Json
  if($previous.input_root.TrimEnd('\','/') -ine $game){continue}
  $recordGroups=@($previous.records|Where-Object status -eq 'loaded'|Group-Object requested_path)
  foreach($group in $recordGroups){
   $package=$group.Name;$names=@($group.Group|ForEach-Object object_path)
   $exports=@($previous.exports|Where-Object {$_.success -and $_.object_path -in $names})
   $files=@($exports|ForEach-Object files|Sort-Object -Unique)
   if($files.Count -eq 0 -or @($files|Where-Object {-not [IO.File]::Exists($_)}).Count -gt 0){continue}
   $previousExports[$package]=[pscustomobject]@{records=@($group.Group);exports=$exports;files=$files;report=$file.FullName;validation=(Join-Path $file.DirectoryName 'export_validation.json')}
  }
 }
}

function Get-JsonObjects([string]$Path){return @(Get-Content -LiteralPath $Path -Raw|ConvertFrom-Json)}
function Resource-JsonFiles($Resource){return @($Resource.files|Where-Object file -like '*.json'|ForEach-Object {Join-Path $repositoryRoot $_.file})}
function Invoke-ExportStage([string]$Stage,[string[]]$Paths,[string]$Mode,[bool]$Meshes){
 $maximum=if($Meshes){$MeshBatchSize}else{$DependencyBatchSize}
 if($Paths.Count -gt $maximum){
  for($batchIndex=0;$batchIndex -lt $Paths.Count;$batchIndex+=$maximum){
   $last=[Math]::Min($Paths.Count-1,$batchIndex+$maximum-1)
   Invoke-ExportStage ($Stage+'_'+('{0:D3}' -f (1+[int]($batchIndex/$maximum)))) @($Paths[$batchIndex..$last]) $Mode $Meshes
   [GC]::Collect();[GC]::WaitForPendingFinalizers();[GC]::Collect()
  }
  return
 }
 $pathsToExport=[Collections.Generic.List[string]]::new()
 foreach($path in $Paths){
  if($resources.ContainsKey($path)){continue}
  if($previousExports.ContainsKey($path)){
   $cached=$previousExports[$path]
   $hasJson=@($cached.files|Where-Object {$_ -like '*.json'}).Count -gt 0
   $hasRequired=if($Meshes){@($cached.files|Where-Object {$_ -match '\.pskx?$'}).Count -gt 0}elseif($Mode -eq 'JsonAndTextures'){@($cached.files|Where-Object {$_ -match '\.(png|hdr)$'}).Count -gt 0}else{$true}
   $validData=$true
   if(-not $SkipValidation -and ($Meshes -or $Mode -eq 'JsonAndTextures')){
    $validData=$false
    if([IO.File]::Exists($cached.validation)){
     $checks=Get-Content -LiteralPath $cached.validation -Raw|ConvertFrom-Json
     $binaryFiles=@($cached.files|Where-Object {if($Meshes){$_ -match '\.pskx?$'}else{$_ -match '\.(png|hdr)$'}})
     $validData=$binaryFiles.Count -gt 0
     foreach($binary in $binaryFiles){if(@($checks.results|Where-Object {$_.file -ieq $binary -and $_.status -eq 'pass'}).Count -eq 0){$validData=$false}}
    }
   }
   if($hasJson -and $hasRequired -and $validData){
    $resources[$path]=[pscustomobject]@{source_package=$path;status='exported';resumed_from_same_input_tree=$true;files=@($cached.files|ForEach-Object {File-Evidence $_ 'new_export'});source_records=$cached.records;export_report=[IO.Path]::GetRelativePath($repositoryRoot,$cached.report).Replace('\','/');validation_report=if([IO.File]::Exists($cached.validation)){[IO.Path]::GetRelativePath($repositoryRoot,$cached.validation).Replace('\','/')}else{$null}}
    if(-not $resumedReports.ContainsKey($cached.report)){
     $resumedReports[$cached.report]=[pscustomobject]@{stage=('resumed_'+[IO.Path]::GetFileName([IO.Path]::GetDirectoryName($cached.report)));status='resumed_same_input_tree';source_packages=[Collections.Generic.List[string]]::new();report=[IO.Path]::GetRelativePath($repositoryRoot,$cached.report).Replace('\','/');validation_report=if([IO.File]::Exists($cached.validation)){[IO.Path]::GetRelativePath($repositoryRoot,$cached.validation).Replace('\','/')}else{$null}}
    }
    $resumedReports[$cached.report].source_packages.Add($path)
    continue
   }
  }
  if($existing.ContainsKey($path)){
   $entry=$existing[$path]
   $hasRequired=if($Meshes){@($entry.files|Where-Object {$_.file -match '\.pskx?$'}).Count -gt 0}elseif($Mode -eq 'JsonAndTextures'){@($entry.files|Where-Object {$_.file -match '\.(png|hdr)$'}).Count -gt 0}else{$true}
   if($hasRequired){$resources[$path]=[pscustomobject]@{source_package=$path;status='referenced_existing';files=$entry.files;source_record=$entry.source_record};continue}
  }
  $pathsToExport.Add($path)
 }
 if($pathsToExport.Count -eq 0){return}
 $stageDirectory=Join-Path $output $Stage
 $previousReportPath=Join-Path $stageDirectory 'export_report.json'
 if([IO.File]::Exists($previousReportPath)){
  $previousStage=Get-Content -LiteralPath $previousReportPath -Raw|ConvertFrom-Json
  $previousStagePaths=@($previousStage.records|ForEach-Object requested_path|Sort-Object -Unique)
  if(@(Compare-Object @($pathsToExport|Sort-Object) $previousStagePaths).Count -gt 0){
   $pathBytes=[Text.Encoding]::UTF8.GetBytes((@($pathsToExport|Sort-Object)-join "`n"))
   $pathDigest=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($pathBytes)).Substring(0,10).ToLowerInvariant()
   $stageDirectory=Join-Path $output ($Stage+'_'+$pathDigest)
  }
 }
 if($PlanOnly){
  $steps.Add([pscustomobject]@{stage=$Stage;status='planned';source_packages=@($pathsToExport);mode=$Mode;export_meshes=$Meshes})
  return
 }
 $null=[IO.Directory]::CreateDirectory($stageDirectory)
 $arguments=@{InputDirectory=$game;ObjectPaths=@($pathsToExport);OutputDirectory=$stageDirectory;CacheDirectory=$CacheDirectory;Mode=$Mode}
 if($Meshes){$arguments.ExportMeshes=$true}
 try{
  $exportReportPath=Join-Path $stageDirectory 'export_report.json'
  $useCache=$false
  if($Resume -and [IO.File]::Exists($exportReportPath)){
   $cachedReport=Get-Content -LiteralPath $exportReportPath -Raw|ConvertFrom-Json
   $cachedPaths=@($cachedReport.records|ForEach-Object requested_path|Sort-Object -Unique)
   $samePaths=@(Compare-Object @($pathsToExport|Sort-Object) $cachedPaths).Count -eq 0
   $useCache=$samePaths -and $cachedReport.input_root.TrimEnd('\','/') -ieq $game -and @($cachedReport.records|Where-Object status -eq 'failed').Count -eq 0 -and @($cachedReport.exports|Where-Object {-not $_.success}).Count -eq 0 -and @($cachedReport.exports|ForEach-Object files|Where-Object {-not [IO.File]::Exists($_)}).Count -eq 0
  }
  Write-Json (Join-Path $output 'collection_progress.json') ([ordered]@{status='running';updated_at_utc=[DateTime]::UtcNow.ToString('o');stage=$Stage;cached_stage=$useCache;stage_packages=@($pathsToExport);resources_completed=$resources.Count;failure_count=$failures.Count;existing_reuse_enabled=(-not $NoExistingReuse)})
  Write-Output ('Collecting stage '+$Stage+' ('+$pathsToExport.Count+' packages), cached='+$useCache)
  if(-not $useCache){& $exporter @arguments|Out-Host}
  $exportReport=Get-Content -LiteralPath $exportReportPath -Raw|ConvertFrom-Json
  foreach($failure in @($exportReport.records|Where-Object status -eq 'failed')){Add-Failure $Stage $failure.requested_path $failure.error}
  foreach($failure in @($exportReport.exports|Where-Object {-not $_.success})){Add-Failure $Stage (ConvertTo-Package $failure.object_path) ([string]$failure.error)}
  foreach($path in $pathsToExport){
   $records=@($exportReport.records|Where-Object {$_.requested_path -ieq $path -and $_.status -eq 'loaded'})
   $objectNames=@($records|ForEach-Object object_path)
   $exports=@($exportReport.exports|Where-Object {$_.success -and $_.object_path -in $objectNames})
   $files=@($exports|ForEach-Object files|Sort-Object -Unique|ForEach-Object {File-Evidence $_ 'new_export'})
   $hasJson=@($files|Where-Object file -like '*.json').Count -gt 0
   $hasRequired=if($Meshes){@($files|Where-Object {$_.file -match '\.pskx?$'}).Count -gt 0}elseif($Mode -eq 'JsonAndTextures'){@($files|Where-Object {$_.file -match '\.(png|hdr)$'}).Count -gt 0}else{$true}
   if($records.Count -eq 0 -or -not $hasJson -or -not $hasRequired){Add-Failure $Stage $path 'Missing required loaded metadata, JSON, ActorX or PNG export.'}
   $resources[$path]=[pscustomobject]@{source_package=$path;status=if($records.Count -gt 0 -and $hasJson -and $hasRequired){'exported'}else{'failed'};files=$files;source_records=$records;export_report=[IO.Path]::GetRelativePath($repositoryRoot,$exportReportPath).Replace('\','/')}
  }
  $validationFile=$null
  if(-not $SkipValidation -and ($Meshes -or $Mode -eq 'JsonAndTextures')){
   $validationFile=Join-Path $stageDirectory 'export_validation.json'
   $validationCacheValid=$false
   if($useCache -and [IO.File]::Exists($validationFile)){
    $previousValidation=Get-Content -LiteralPath $validationFile -Raw|ConvertFrom-Json
    $validationCacheValid=@($previousValidation.results).Count -gt 0 -and @($previousValidation.results|Where-Object status -ne 'pass').Count -eq 0
   }
   if(-not $validationCacheValid){& $validator -ReportFile $exportReportPath -CacheDirectory $CacheDirectory|Out-Host}
   $validation=Get-Content -LiteralPath $validationFile -Raw|ConvertFrom-Json
   foreach($failure in @($validation.results|Where-Object status -ne 'pass')){Add-Failure ($Stage+'/validation') (ConvertTo-Package $failure.object_path) ([string](Get-Optional $failure 'error' 'Validation failed.'))}
   if(@($validation.results).Count -eq 0){Add-Failure ($Stage+'/validation') '' 'No ActorX/PNG validation results were produced.'}
  }
  $steps.Add([pscustomobject]@{stage=$Stage;status='executed';source_packages=@($pathsToExport);report=[IO.Path]::GetRelativePath($repositoryRoot,$exportReportPath).Replace('\','/');validation_report=if($validationFile){[IO.Path]::GetRelativePath($repositoryRoot,$validationFile).Replace('\','/')}else{$null}})
 }catch{Add-Failure $Stage '' $_.Exception.ToString();$steps.Add([pscustomobject]@{stage=$Stage;status='failed';source_packages=@($pathsToExport);error=$_.Exception.Message})}
}
function Queue-Material([string]$Package){if($Package -and -not $materialSeen.Contains($Package)){$materialQueue.Enqueue($Package)}}
function Find-TextureReferences($Value,[string]$Trail,[string]$Owner,[string]$JsonFile){
 if($null -eq $Value -or $Value -is [string] -or $Value -is [ValueType]){return}
 if($Value -is [Collections.IEnumerable] -and $Value -isnot [Collections.IDictionary] -and $Value -isnot [pscustomobject]){
  $i=0;foreach($item in $Value){Find-TextureReferences $item ($Trail+'['+$i+']') $Owner $JsonFile;$i++};return
 }
 $objectName=[string](Get-Optional $Value 'ObjectName' '')
 $objectPath=[string](Get-Optional $Value 'ObjectPath' '')
 if($objectPath -and ($objectName -match '^(Texture2D|TextureCube|VirtualTexture|Texture)' -or $Trail -match '(?i)TextureParameterValues|TextureValue|TextureReference')){
  $source=ConvertTo-Package $objectPath
  if($source){$null=$texturePaths.Add($source);$materialEdges.Add([pscustomobject]@{material_package=$Owner;kind='explicit_texture_reference';json_file=$JsonFile;property_trail=$Trail;object_name=$objectName;object_path=$objectPath;texture_package=$source;binding_inferred=$false})}
 }
 if($Value -is [Collections.IDictionary]){foreach($key in $Value.Keys){Find-TextureReferences $Value[$key] ($Trail+'.'+$key) $Owner $JsonFile};return}
 foreach($property in $Value.PSObject.Properties){Find-TextureReferences $property.Value ($Trail+'.'+$property.Name) $Owner $JsonFile}
}

try{
 Write-Json (Join-Path $output 'collection_progress.json') ([ordered]@{status='running';started_at_utc=[DateTime]::UtcNow.ToString('o');planned_mesh_packages=$orderedMeshes.Count;existing_reuse_enabled=(-not $NoExistingReuse);mesh_batch_size=$MeshBatchSize})
 Invoke-ExportStage 'Meshes' $orderedMeshes 'Json' $true
 if(-not $PlanOnly){
  foreach($path in $orderedMeshes){
   if(-not $resources.ContainsKey($path)){continue}
   foreach($json in (Resource-JsonFiles $resources[$path])){
    foreach($object in (Get-JsonObjects $json)){
     if((Get-Optional $object 'Type') -notin @('SkeletalMesh','SkinnedAsset')){continue}
     $jsonRelative=[IO.Path]::GetRelativePath($repositoryRoot,$json).Replace('\','/')
     $slots=@(Get-Optional $object 'SkeletalMaterials' @());$index=0
     foreach($slot in $slots){
      $reference=Get-Optional $slot 'Material';$source=ConvertTo-Package ([string](Get-Optional $reference 'ObjectPath' ''))
      $meshEdges.Add([pscustomobject]@{mesh_package=$path;mesh_export_name=$object.Name;json_file=$jsonRelative;slot_index=$index;slot_name=(Get-Optional $slot 'MaterialSlotName');object_name=(Get-Optional $reference 'ObjectName');object_path=(Get-Optional $reference 'ObjectPath');material_package=$source;status=if($source){'explicit_reference'}else{'null_or_unresolved'}})
      if($source){Queue-Material $source};$index++
     }
     $properties=Get-Optional $object 'Properties'
     $deferred.Add([pscustomobject]@{kind='mesh_skeleton_reference';mesh_package=$path;json_file=$jsonRelative;skeleton=(Get-Optional $properties 'Skeleton');status='retained_not_animation_converted'})
    }
   }
  }
  for($round=1;$materialQueue.Count -gt 0 -and $round -le $MaximumDependencyRounds;$round++){
   $batch=[Collections.Generic.List[string]]::new()
   while($materialQueue.Count -gt 0){
    $path=$materialQueue.Dequeue();if(-not $materialSeen.Add($path)){continue}
    if($materialSeen.Count -gt $MaterialBudget){Add-Failure 'Materials/budget' $path 'Material dependency budget exceeded.';continue}
    $batch.Add($path)
   }
   if($batch.Count -eq 0){break}
   Invoke-ExportStage ('Materials_{0:D2}' -f $round) @($batch) 'Json' $false
   foreach($path in $batch){
    if(-not $resources.ContainsKey($path)){continue}
    foreach($json in (Resource-JsonFiles $resources[$path])){
     foreach($object in (Get-JsonObjects $json)){
      $relative=[IO.Path]::GetRelativePath($repositoryRoot,$json).Replace('\','/')
      $properties=Get-Optional $object 'Properties';$parent=Get-Optional $properties 'Parent'
      $parentPath=ConvertTo-Package ([string](Get-Optional $parent 'ObjectPath' ''))
      if($parentPath){$materialEdges.Add([pscustomobject]@{material_package=$path;kind='explicit_parent_reference';json_file=$relative;object_path=(Get-Optional $parent 'ObjectPath');parent_package=$parentPath});Queue-Material $parentPath}
      Find-TextureReferences $object '$' $path $relative
     }
    }
   }
  }
  if($materialQueue.Count -gt 0){Add-Failure 'Materials/round_limit' '' 'Material parent dependency rounds exceeded.'}
  Invoke-ExportStage 'Textures' @($texturePaths|Sort-Object) 'JsonAndTextures' $false
 }
}catch{Add-Failure 'Collection' '' $_.Exception.ToString()}
finally{
 $status=if($failures.Count -gt 0){'failed'}elseif($PlanOnly){'planned_only'}elseif($SkipValidation){'exports_collected_validation_skipped'}else{'collected_and_data_validated'}
 $report=[ordered]@{
  schema_version=1;generated_at_utc=[DateTime]::UtcNow.ToString('o');status=$status
  scope='Offline NZM disk files only. Mesh JSON/ActorX and explicit material/parent/texture references collected. No runtime project assets changed.'
  existing_pcresources_reuse_enabled=(-not $NoExistingReuse)
  unavailable_config_references=@(Get-Optional $sourcePlan 'unavailable_mesh_references' @())
  input_root=$game;inputs=@($inputEvidence);mesh_package_count=$orderedMeshes.Count;material_dependency_count=$materialSeen.Count;texture_dependency_count=$texturePaths.Count
  resource_count=$resources.Count;referenced_existing_count=@($resources.Values|Where-Object status -eq 'referenced_existing').Count;newly_exported_count=@($resources.Values|Where-Object status -eq 'exported').Count;failed_count=$failures.Count
  completeness='This collection covers the supplied mesh plan and explicit material-parent/texture references within budget. Animation routes and skeleton configuration are retained; full animation conversion and UE reconstruction are not completed.'
  limitations=@('Input PlanJson is retained verbatim; gender and outfit completeness depend on its configuration evidence. Filename-only discovery is not a complete roster assertion.','Mesh inline exports remain in JSON; only explicit SkeletalMaterials references create material dependencies.','Textures are included by explicit serialized references; nearby Textures/Textures_HD folders are not automatically assigned to mesh slots.','Source selection follows the stated offline provider input tree; live game update mounting priority remains unverified.','Collected PSK weights/points and decoded PNG dimensions are validated when validation is enabled. No UE import, animation playback or visual validation is implied.','Shared source files already present in PCResources are referenced by exact source package and SHA256 instead of copied again.')
  steps=(@($steps)+@($resumedReports.Values));mesh_material_edges=@($meshEdges);material_dependency_edges=@($materialEdges);deferred_configuration=@($deferred);resources=@($resources.Values|Sort-Object source_package);failures=@($failures)
 }
 Write-Json (Join-Path $output 'character_resource_manifest.json') $report
 Write-Json (Join-Path $output 'collection_progress.json') ([ordered]@{status=$status;finished_at_utc=[DateTime]::UtcNow.ToString('o');planned_mesh_packages=$orderedMeshes.Count;resources_completed=$resources.Count;failure_count=$failures.Count;manifest='character_resource_manifest.json'})
 [pscustomobject]@{status=$status;mesh_packages=$orderedMeshes.Count;materials=$materialSeen.Count;textures=$texturePaths.Count;resources=$resources.Count;failed=$failures.Count;manifest=(Join-Path $output 'character_resource_manifest.json')}|ConvertTo-Json -Compress
}
if($failures.Count -gt 0){exit 1}
