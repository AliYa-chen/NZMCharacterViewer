<#
.SYNOPSIS
Read character configuration tables and resolve explicit female avatar routes.
.DESCRIPTION
Only reads offline Pak files. No process, memory, debugger, injection, game
launch, or anticheat APIs are used. Gender and outfit labels require serialized
configuration evidence; resource directory names alone are not treated as proof.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$GameDirectory,
    [string]$CacheDirectory,
    [string]$OutputDirectory,
    [string]$InventoryFile,
    [string]$ReviewedMeshFile
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if(-not$OutputDirectory){$OutputDirectory=Join-Path $repo 'Saved/PCAssetAnalysis/ExpandedCharacters'}
if(-not$InventoryFile){$InventoryFile=Join-Path $OutputDirectory 'inventory.json'}
if(-not$ReviewedMeshFile){$ReviewedMeshFile=Join-Path $PSScriptRoot 'reviewed_mesh_packages.json'}
$outputRoot=[IO.Path]::GetFullPath($OutputDirectory)
$gameRoot=(Resolve-Path -LiteralPath $GameDirectory).Path.TrimEnd('\','/')
if($outputRoot.Equals($gameRoot,[StringComparison]::OrdinalIgnoreCase)-or$outputRoot.StartsWith($gameRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Output must be outside the read-only game tree.'}
[IO.Directory]::CreateDirectory($outputRoot)|Out-Null
. (Join-Path $PSScriptRoot '../Runtime/Import-Runtime.ps1') -CacheDirectory $CacheDirectory|Out-Null
[CUE4Parse.Compression.OodleHelper]::Initialize((Join-Path $NZMRuntimeCacheRoot 'Native/oodle-data-shared.dll'))
$versions=[CUE4Parse.UE4.Versions.VersionContainer]::new([CUE4Parse.UE4.Versions.EGame]::GAME_AssaultFireFuture)
$provider=[CUE4Parse.FileProvider.DefaultFileProvider]::new($gameRoot,[IO.SearchOption]::AllDirectories,$versions,[StringComparer]::OrdinalIgnoreCase)
$tablePackages=@(
    '/Game/DataTables/Avatar/Character/NZAvatarModelConfig_Character',
    '/Game/DataTables/Avatar/Character/NZAvatarPartConfig_Character',
    '/Game/DataTables/CharacterPersonality/RoleSkinInfo',
    '/Game/DataTables/SkinItemTable',
    '/Game/DataTables/LuaDataTable/RoleVariantTable',
    '/Game/DataTables/CharacterPersonality/RoleCard',
    '/Game/DataTables/Avatar/Character/NZAvatarPartCustomReplaceConfig_Character'
)
$tables=@{}
$baseTables=@{}
$configVariant='provider_selected_all_archives'
$configCache=@{}
$sources=[Collections.Generic.List[object]]::new()
$loadErrors=[Collections.Generic.List[object]]::new()
$models=[Collections.Generic.List[object]]::new()
$allDataAssetPaths=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
function Property($Object,[string]$Name){if($null-eq$Object){return $null};$p=$Object.PSObject.Properties[$Name];if($null-ne$p){return $p.Value};return $null}
function To-GamePackage([string]$Path){$Path=($Path-replace'\.(uasset|umap)$','');$Path=($Path-replace'\.[^/]+$','');return $Path.Replace('NZM/Content/','/Game/')}
function Get-PackageSources([string]$Package){
    $fixed=$provider.FixPath($Package)-replace'\.(uasset|umap)$','';$files=[ordered]@{}
    foreach($extension in @('uasset','uexp','ubulk')){
        [CUE4Parse.FileProvider.Objects.GameFile]$file=$null
        if($provider.TryGetGameFile($fixed+'.'+$extension,[ref]$file)){
            $entry=[ordered]@{virtual_path=$file.Path;size=$file.Size;compression=$file.CompressionMethod.ToString()}
            if($file-is[CUE4Parse.UE4.Pak.Objects.FPakEntry]){$entry.archive=[IO.Path]::GetRelativePath($gameRoot,$file.Vfs.Path).Replace('\','/');$entry.read_order=$file.Vfs.ReadOrder;$entry.offset=$file.Offset}
            $files[$extension]=$entry
        }
    }
    return $files
}
function Load-Config([string]$Package,[string]$ExpectedName){
    $cacheKey=$configVariant+'|'+$Package
    if($configCache.ContainsKey($cacheKey)){return $configCache[$cacheKey]}
    try{
        $exports=@($provider.LoadPackageObjects($Package))
        $export=$exports|Where-Object Name -eq $ExpectedName|Select-Object -First 1
        if($null-eq$export){throw 'Expected top-level object was not found.'}
        $json=[Newtonsoft.Json.JsonConvert]::SerializeObject($export,[Newtonsoft.Json.Formatting]::Indented)
        $parsed=$json|ConvertFrom-Json
        $relative=$Package.Replace('/Game/','').Replace('NZM/Content/','')+'.json'
        $destination=Join-Path $outputRoot ('ConfigEvidence/'+$configVariant+'/'+$relative)
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))|Out-Null
        [IO.File]::WriteAllText($destination,$json,[Text.UTF8Encoding]::new($false))
        $sources.Add([ordered]@{virtual_package=$Package;source_variant=$configVariant;class=$export.ExportType;file=[IO.Path]::GetRelativePath($outputRoot,$destination).Replace('\','/');sha256=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant();selected_files=(Get-PackageSources $Package)})
        $configCache[$cacheKey]=$parsed
        return $parsed
    }catch{$loadErrors.Add([ordered]@{package=$Package;error=$_.Exception.Message});return $null}
}
try{
    $provider.Initialize();$mounted=$provider.SubmitKey([Activator]::CreateInstance([CUE4Parse.UE4.Objects.Core.Misc.FGuid]),[CUE4Parse.Encryption.Aes.FAesKey]::new('0x'+('0'*64)))
    $selectedProvider=$provider
    $baseProvider=[CUE4Parse.FileProvider.DefaultFileProvider]::new((Join-Path $gameRoot 'Content/Paks'),[IO.SearchOption]::TopDirectoryOnly,$versions,[StringComparer]::OrdinalIgnoreCase)
    $baseProvider.Initialize();$baseMounted=$baseProvider.SubmitKey([Activator]::CreateInstance([CUE4Parse.UE4.Objects.Core.Misc.FGuid]),[CUE4Parse.Encryption.Aes.FAesKey]::new('0x'+('0'*64)))
    $provider=$baseProvider;$configVariant='base_content_paks'
    foreach($package in $tablePackages){$name=$package.Split('/')[-1];$baseTables[$name]=Load-Config $package $name}
    $provider=$selectedProvider;$configVariant='provider_selected_all_archives'
    foreach($package in $tablePackages){$name=$package.Split('/')[-1];$tables[$name]=Load-Config $package $name}
    if($null-eq$tables.NZAvatarModelConfig_Character-or$null-eq$tables.NZAvatarPartConfig_Character){throw 'Required model/part table could not be loaded.'}
    $modelRows=[ordered]@{};$partRows=[ordered]@{};$modelRowVersions=@{}
    foreach($variantSpec in @(@{name='base_content_paks';tables=$baseTables},@{name='provider_selected_all_archives';tables=$tables})){
        foreach($row in $variantSpec.tables.NZAvatarModelConfig_Character.Rows.PSObject.Properties){$modelRows[$row.Name]=$row;if(-not$modelRowVersions.ContainsKey($row.Name)){$modelRowVersions[$row.Name]=@()};$modelRowVersions[$row.Name]+=[ordered]@{source_variant=$variantSpec.name;configuration=$row.Value}}
        foreach($row in $variantSpec.tables.NZAvatarPartConfig_Character.Rows.PSObject.Properties){$partRows[$row.Name]=$row}
    }
    $filePaths=@(($selectedProvider.Files.Keys+$baseProvider.Files.Keys)|Where-Object{$_-match'(?i)/DA_Char_[^/]+\.uasset$'}|Sort-Object -Unique)
    foreach($row in $modelRows.Values){
        $id=$row.Name;$config=$row.Value;$assetName=$config.AvatarAssetId.PrimaryAssetName
        $matching=@($filePaths|Where-Object{[IO.Path]::GetFileNameWithoutExtension($_)-eq$assetName}|ForEach-Object{To-GamePackage $_}|Sort-Object -Unique)
        $dataRows=[Collections.Generic.List[object]]::new()
        foreach($package in $matching){
            [void]$allDataAssetPaths.Add($package)
            foreach($dataVariant in @(@{name='provider_selected_all_archives';provider=$selectedProvider},@{name='base_content_paks';provider=$baseProvider})){
            $provider=$dataVariant.provider;$configVariant=$dataVariant.name
            [CUE4Parse.FileProvider.Objects.GameFile]$configFile=$null
            if(-not$provider.TryGetGameFile($package+'.uasset',[ref]$configFile)){continue}
            $obj=Load-Config $package $assetName
            if($null-eq$obj){continue}
            $gender=Property (Property $obj.Properties 'ModeConfig') 'Gender'
            $routes=[Collections.Generic.List[object]]::new()
            foreach($routeProperty in $obj.Properties.PSObject.Properties|Where-Object{$_.Name-match'Config$'-and$_.Name-ne'ModeConfig'}){
                $partIds=@(Property $routeProperty.Value 'DefaultPartItems'|Where-Object{$null-ne$_})
                $parts=[Collections.Generic.List[object]]::new()
                foreach($partId in $partIds){
                    if([string]::IsNullOrWhiteSpace([string]$partId)){continue}
                    $variantPartTable=if($configVariant-eq'base_content_paks'){$baseTables.NZAvatarPartConfig_Character}else{$tables.NZAvatarPartConfig_Character}
                    $partProperty=$variantPartTable.Rows.PSObject.Properties[[string]$partId]
                    $part=if($null-ne$partProperty){$partProperty.Value}else{$null}
                    $parts.Add([ordered]@{part_id=[string]$partId;status=$(if($null-ne$part){'resolved'}else{'missing_part_row'});configuration=$part;mesh_package=$(if($null-ne$part){To-GamePackage $part.Mesh.AssetPathName}else{$null})})
                }
                $routes.Add([ordered]@{route=$routeProperty.Name;explicit_configuration=$routeProperty.Value;default_parts=@($parts.ToArray());visibility='DefaultPartItems is the serialized default list; optional visibility, runtime conditions and template inheritance are not fully resolved.'})
            }
            $dataRows.Add([ordered]@{virtual_package=$package;source_variant=$configVariant;type=$obj.Type;gender=$gender;gender_basis='explicit ModeConfig.Gender only';bind_tag=(Property (Property (Property $obj.Properties 'ModeConfig') 'BindTag') 'TagName');routes=@($routes.ToArray())})
            }
        }
        $genderValues=@($dataRows.ToArray()|ForEach-Object{$_['gender']}|Where-Object{$_}|Sort-Object -Unique)
        $female=$genderValues-contains'EAvaGenderType::Female'
        $variantProperty=if($null-ne$tables.RoleVariantTable){$tables.RoleVariantTable.Rows.PSObject.Properties[$id]}else{$null}
        $variant=if($null-ne$variantProperty){$variantProperty.Value}else{$null}
        $models.Add([ordered]@{model_id=$id;description=$config.Description;b_is_cook=$config.bIsCook;primary_asset_name=$assetName;candidate_asset_packages=$matching;gender_status=$(if($female){'explicit_female'}elseif($genderValues.Count-gt0){'explicit_non_female'}else{'unknown_missing_explicit_gender'});gender_values=$genderValues;variant_row=$variant;model_row=$config;model_row_versions=$modelRowVersions[$id];data_assets=@($dataRows.ToArray())})
    }
    $femaleModels=@($models.ToArray()|Where-Object{$_['gender_status']-eq'explicit_female'})
    $unknownModels=@($models.ToArray()|Where-Object{$_['gender_status']-like'unknown*'})
    $femaleRoots=@($femaleModels|ForEach-Object{$_['data_assets']}|ForEach-Object{$_['virtual_package']}|ForEach-Object{if($_-match'/Avatar/Character/([^/]+)/'){$Matches[1]}}|Sort-Object -Unique)
    $relatedParts=@($partRows.Values|Where-Object{[string]$_.Value.Mesh.AssetPathName-match'/Avatar/Character/('+($femaleRoots-join'|')+')/'}|ForEach-Object{[ordered]@{part_id=$_.Name;configuration=$_.Value;mesh_package=(To-GamePackage $_.Value.Mesh.AssetPathName)}})
    $provider=$selectedProvider
    $inventory=$null;if(Test-Path -LiteralPath $InventoryFile){$inventory=Get-Content -LiteralPath $InventoryFile -Raw|ConvertFrom-Json}
    $groups=@();if($null-ne$inventory){$groups=@($inventory.groups|Where-Object character_root -in $femaleRoots)}
    $femaleCustomRows=[Collections.Generic.List[object]]::new()
    foreach($variantSpec in @(@{name='base_content_paks';tables=$baseTables},@{name='provider_selected_all_archives';tables=$tables})){
        foreach($customRow in $variantSpec.tables.NZAvatarPartCustomReplaceConfig_Character.Rows.PSObject.Properties){
            $tag=[string](Property (Property $customRow.Value 'AvatarModelTag') 'TagName')
            if($tag-notmatch('^Avatar\.Character\.('+($femaleRoots-join'|')+')$')){continue}
            $customParts=[Collections.Generic.List[object]]::new()
            foreach($route in $customRow.Value.PSObject.Properties|Where-Object Name -match 'ConfigID$'){
                foreach($partId in @(Property $route.Value 'PartID'|Where-Object{$_})){
                    $partProperty=$variantSpec.tables.NZAvatarPartConfig_Character.Rows.PSObject.Properties[[string]$partId]
                    $part=if($null-ne$partProperty){$partProperty.Value}else{$null}
                    $customParts.Add([ordered]@{route=$route.Name;part_id=[string]$partId;configuration=$part;mesh_package=$(if($null-ne$part){To-GamePackage $part.Mesh.AssetPathName}else{$null})})
                }
            }
            $femaleCustomRows.Add([ordered]@{row_id=$customRow.Name;source_variant=$variantSpec.name;avatar_tag=$tag;configuration=$customRow.Value;parts=@($customParts.ToArray())})
        }
    }
    $inventoryMeshPaths=@();if($null-ne$inventory){$inventoryMeshPaths=@($inventory.mesh_candidates|Where-Object character_root -in $femaleRoots|ForEach-Object{$_.requested_path})}
    $defaultMeshPaths=@($femaleModels|ForEach-Object{$_['data_assets']}|ForEach-Object{$_['routes']}|ForEach-Object{$_['default_parts']}|ForEach-Object{$_['mesh_package']})
    $customMeshPaths=@($femaleCustomRows.ToArray()|ForEach-Object{$_['parts']}|ForEach-Object{$_['mesh_package']})
    $configuredMeshPaths=@(($inventoryMeshPaths+$defaultMeshPaths+$customMeshPaths+@($relatedParts|ForEach-Object{$_['mesh_package']}))|Where-Object{$_-and$_-ne'None'-and$_-match'^/Game/.+/[^/]+$'}|Sort-Object -Unique)
    # A reviewed package is an explicit exception to filename discovery, never
    # implicit gender or playable-roster evidence. Decode it on every run and
    # require the reviewed class/skeleton reference to remain true.
    $reviewedMeshes=[Collections.Generic.List[object]]::new()
    $reviewedMeshPaths=[Collections.Generic.List[string]]::new()
    if(Test-Path -LiteralPath $ReviewedMeshFile){
        $reviewed=Get-Content -LiteralPath $ReviewedMeshFile -Raw|ConvertFrom-Json
        foreach($entry in $reviewed.packages){
            $package=To-GamePackage $entry.mesh_package
            $obj=Load-Config $package $package.Split('/')[-1]
            if($null-eq$obj-or$obj.Type-ne'SkeletalMesh'){throw ('Reviewed mesh no longer decodes as SkeletalMesh: '+$package)}
            $skeleton=To-GamePackage (Property (Property $obj.Properties 'Skeleton') 'ObjectPath')
            if($entry.expected_skeleton_package-and$skeleton-ne$entry.expected_skeleton_package){throw ('Reviewed mesh skeleton changed: '+$package)}
            $materials=@(Property $obj 'SkeletalMaterials')
            $expectedSlots=Property $entry 'expected_material_slots'
            $materialPattern=Property $entry 'expected_material_path_pattern'
            if($null-ne$expectedSlots-and$materials.Count-ne$expectedSlots){throw ('Reviewed mesh material count changed: '+$package)}
            if($materialPattern-and@($materials|Where-Object{$_.Material.ObjectPath-notmatch$materialPattern}).Count-gt0){throw ('Reviewed mesh material reference changed: '+$package)}
            $reviewedMeshPaths.Add($package)
            $reviewedMeshes.Add([ordered]@{mesh_package=$package;classification=$entry.classification;character_root=$entry.character_root;gender_basis=$entry.gender_basis;reason=$entry.reason;decoded_class=$obj.Type;skeleton_package=$skeleton;material_slots=$materials.Count;material_references=$materials;source_variant=$configVariant;selected_files=(Get-PackageSources $package)})
        }
    }
    $allMeshPaths=@(($configuredMeshPaths+@($reviewedMeshPaths.ToArray()))|Sort-Object -Unique)
    $availableMeshes=[Collections.Generic.List[string]]::new()
    $unavailableMeshes=[Collections.Generic.List[string]]::new()
    $meshAvailability=[Collections.Generic.List[object]]::new()
    foreach($package in $allMeshPaths){
        $selectedFiles=Get-PackageSources $package
        $available=$selectedFiles.Contains('uasset')
        if($available){$availableMeshes.Add($package)}else{$unavailableMeshes.Add($package)}
        $meshAvailability.Add([ordered]@{mesh_package=$package;status=$(if($available){'installed_provider_package'}else{'unavailable_configuration_reference'});selected_files=$selectedFiles})
    }
    $meshPlan=[ordered]@{
        schema_version=2;scope='Installed packages from explicitly female root inventory, serialized default/custom/related part references, and separately reviewed source meshes. Missing references are retained separately.'
        source_evidence='outfit_config_evidence.json';explicit_female_roots=$femaleRoots
        mesh_paths=@($availableMeshes.ToArray());unavailable_mesh_references=@($unavailableMeshes.ToArray());all_candidate_mesh_paths=$allMeshPaths
        default_route_mesh_paths=@($defaultMeshPaths|Where-Object{$_-and$_-ne'None'}|Sort-Object -Unique)
        custom_replacement_mesh_paths=@($customMeshPaths|Where-Object{$_-and$_-ne'None'}|Sort-Object -Unique)
        inventory_candidate_mesh_paths=@($inventoryMeshPaths|Sort-Object -Unique)
        reviewed_mesh_packages=@($reviewedMeshes.ToArray());source_only_mesh_paths=@($reviewedMeshes.ToArray()|Where-Object{$_['classification']-eq'source_only_female_base'}|ForEach-Object{$_['mesh_package']})
        counts=[ordered]@{all_mesh_packages=$availableMeshes.Count;all_referenced_or_candidate_mesh_packages=$allMeshPaths.Count;unavailable_mesh_references=$unavailableMeshes.Count;reviewed_additional_mesh_packages=$reviewedMeshes.Count;female_roots=$femaleRoots.Count;model_rows=$femaleModels.Count;custom_replacement_rows=$femaleCustomRows.Count}
        limitations=@('mesh_paths includes installed packages only; availability is a provider-file check, not successful mesh conversion.','unavailable_mesh_references preserves configuration paths absent from this installation; these must not be treated as export failures.','Inventory names are candidates until decoded as meshes; reviewed additional packages are class/skeleton checked on every run.','The plan includes UI, CutScene, Preview, 1P, 3P, masters and accessories to avoid dropping alternate parts; they are not all simultaneously visible.','Default parts, optional/replacement parts, source-only resources and current player availability are distinct.','No current runtime patch-priority or playable roster claim is made.')
    }
    [IO.File]::WriteAllText((Join-Path $outputRoot 'female_mesh_plan.json'),($meshPlan|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
    $evidence=[ordered]@{
        schema_version=2;status='offline_character_configuration_evidence';mounted_archives=$mounted;provider_files=$provider.Files.Count
        selected_scope='Model, part, role variant/card/skin and custom replacement configuration only; source files are selected by offline provider. Current game patch priority is not independently verified.'
        sources=@($sources.ToArray());load_errors=@($loadErrors.ToArray());table_count=$tablePackages.Count;data_asset_package_count=$allDataAssetPaths.Count
        table_variants=[ordered]@{base_mounted_archives=$baseMounted;base_model_rows=@($baseTables.NZAvatarModelConfig_Character.Rows.PSObject.Properties).Count;selected_model_rows=@($tables.NZAvatarModelConfig_Character.Rows.PSObject.Properties).Count;union_model_rows=$modelRows.Count;comparison='Base and provider-selected table variants retained. Union avoids dropping installed resources removed from one table version; current playable/selected availability is not established.'}
        summary=[ordered]@{model_rows=$models.Count;explicit_female_models=$femaleModels.Count;explicit_female_roots=$femaleRoots.Count;unknown_gender_models=@($models.ToArray()|Where-Object{$_['gender_status']-like'unknown*'}).Count;female_related_part_rows=$relatedParts.Count}
        explicit_female_roots=$femaleRoots;female_models=$femaleModels;all_model_gender_evidence=@($models.ToArray());female_related_part_rows=$relatedParts
        unknown_gender_models=$unknownModels
        unknown_gender_summary=@($unknownModels|ForEach-Object{[ordered]@{model_id=$_['model_id'];description=$_['description'];primary_asset_name=$_['primary_asset_name'];candidate_asset_packages=$_['candidate_asset_packages'];reason=$(if($_['primary_asset_name']-eq'None'-or-not$_['primary_asset_name']){'Model row has no PrimaryAssetName reference; no gender inference.'}elseif($_['candidate_asset_packages'].Count-eq0){'Referenced DA basename not found in either inspected provider file set; no name-based gender inference.'}else{'DA exists but has no explicit ModeConfig.Gender in inspected serialized properties.'})}})
        female_custom_replacement_rows=@($femaleCustomRows.ToArray());mesh_plan='female_mesh_plan.json'
        reviewed_mesh_packages=@($reviewedMeshes.ToArray());mesh_package_availability=@($meshAvailability.ToArray())
        mesh_plan_summary=$meshPlan.counts
        reviewed_mesh_input=[ordered]@{file=[IO.Path]::GetRelativePath($repo,[IO.Path]::GetFullPath($ReviewedMeshFile)).Replace('\','/');sha256=$(if(Test-Path -LiteralPath $ReviewedMeshFile){(Get-FileHash -LiteralPath $ReviewedMeshFile -Algorithm SHA256).Hash.ToLowerInvariant()}else{$null})}
        inventory_input=$(if(Test-Path -LiteralPath $InventoryFile){[ordered]@{file=[IO.Path]::GetRelativePath($repo,[IO.Path]::GetFullPath($InventoryFile)).Replace('\','/');sha256=(Get-FileHash -LiteralPath $InventoryFile -Algorithm SHA256).Hash.ToLowerInvariant()}}else{$null})
        female_root_inventory_groups=$groups
        special_findings=@(
            [ordered]@{subject='Bunny';model_id='10002000056';status='configuration_verified_separate_female_character';evidence='Model Description=芭比, DA_Char_Bunny ModeConfig.Gender=Female and BindTag=Avatar.Character.Bunny; PartConfig rabbit-ear slot is explicitly described 芭比-兔耳朵UI. Exact commercial display name remains version-dependent.'},
            [ordered]@{subject='Naimei Suit_A';model_id='10002000045';status='default_ui_route_verified';evidence='DA_Char_Naimei default UI routes to Suit_A body/cloak/shoe weapon/hair accessory and CommonRes head/hair.'},
            [ordered]@{subject='Naimei Suit_C';status='directory_and_part_reference_only';evidence='Part14501080001 is described 奈美-神乐铃UI and references Naimei_Suit_C weapons; directory suffix alone does not identify Bunny or a selectable commercial outfit ID.'}
        )
        limits=@('A serialized female avatar is not by itself proof of current player availability.','No outfit or skin ID is inferred from a resource directory suffix.','Optional parts, dyes, material overrides, template inheritance and conditions remain explicit evidence or unknown; DefaultPartItems are not all possible choices.','Related inventory groups include filename candidates, not automatically selectable outfits.','No process, memory, debugger, game launch or anticheat interaction occurred.')
    }
    $reviewSummaryFile=Join-Path $outputRoot 'ReviewCandidates/review_summary.json'
    if(Test-Path -LiteralPath $reviewSummaryFile){
        $evidence.supplemental_discovery=[ordered]@{file='ReviewCandidates/review_summary.json';sha256=(Get-FileHash -LiteralPath $reviewSummaryFile -Algorithm SHA256).Hash.ToLowerInvariant();review=(Get-Content -LiteralPath $reviewSummaryFile -Raw|ConvertFrom-Json)}
    }
    $configuredReviewFile=Join-Path $outputRoot 'ReviewCandidates/ConfiguredNonSk/export_report.json'
    if(Test-Path -LiteralPath $configuredReviewFile){
        $evidence.shared_configuration_mesh_discovery=[ordered]@{file='ReviewCandidates/ConfiguredNonSk/export_report.json';sha256=(Get-FileHash -LiteralPath $configuredReviewFile -Algorithm SHA256).Hash.ToLowerInvariant();review=(Get-Content -LiteralPath $configuredReviewFile -Raw|ConvertFrom-Json)}
    }
    [IO.File]::WriteAllText((Join-Path $outputRoot 'outfit_config_evidence.json'),($evidence|ConvertTo-Json -Depth 80),[Text.UTF8Encoding]::new($false))
    $missingDaCount=@($unknownModels|Where-Object{$_['primary_asset_name']-and$_['primary_asset_name']-ne'None'-and$_['candidate_asset_packages'].Count-eq0}).Count
    $summaryLines=@(
        '# 女角色配置核对','',
        ('基础包模型表{0}行、含更新包的 provider 选中表{1}行，并集{2}行。{3}行具有明确 ModeConfig.Gender=Female，归属{4}个资源根；含 bot、别名与服装变体，不等于{3}名现役可选角色。' -f $evidence.table_variants.base_model_rows,$evidence.table_variants.selected_model_rows,$models.Count,$femaleModels.Count,$femaleRoots.Count),'',
        ('{0}行缺少明确性别，其中{1}行声明的 DA 不在两个 provider 文件集中；另有无有效 DA 引用及 DA 没有显式 Gender 的条目，逐项见 unknown_gender_summary。Queen_Naked 仍是已确认 Queen 根内的源资源候选。' -f $unknownModels.Count,$missingDaCount),'',
        '| 模型 ID | 配置描述 | DA |','| --- | --- | --- |'
    )
    foreach($model in $femaleModels){$summaryLines+=('| {0} | {1} | {2} |' -f $model['model_id'],([string]$model['description']).Replace('|','\|'),$model['primary_asset_name'])}
    $summaryLines+=@(
        '',
        '兔耳属于独立 Bunny／芭比 10002000056 的 Suit_C 路线。Naimei 10002000045 默认 UI 指向 Suit_A 与 CommonRes；Naimei_Suit_C 的神乐铃、CutScene 候选不能仅按后缀认定为兔女郎或商业服装名。','',
        ('female_mesh_plan.json 共{0}个候选／配置路径：mesh_paths 中{1}个安装内存在的包，unavailable_mesh_references 中{2}个未安装配置引用。默认路线、自定义替换、库存候选与 source-only 来源分别保存；包含 UI／CutScene／Preview／1P／3P／Master／饰件，不表示全部可同时显示。' -f $allMeshPaths.Count,$availableMeshes.Count,$unavailableMeshes.Count),'',
        '补充复核保留 Default_Female_00 基础女模、Angel/Wristband_Mesh，以及艾玛基础表自定义替换引用的共享 Pendant_Base_Neak。前者仅为 source-only，后两者为饰件／共享底座；不新增明确女性模型行。实际类型、骨架、材质和来源见 reviewed_mesh_packages。','',
        '完整 UI／Dialogue／TPP／FPP 默认部件及替换规则见 outfit_config_evidence.json。当前可选性、模板继承、染色、运行时显示条件和商业服装 ID 仍需在目标 UE 机器核验。'
    )
    [IO.File]::WriteAllText((Join-Path $outputRoot 'OUTFIT_CONFIG_SUMMARY.md'),($summaryLines-join[Environment]::NewLine),[Text.UTF8Encoding]::new($false))
    [ordered]@{output=(Join-Path $outputRoot 'outfit_config_evidence.json');mounted_archives=$mounted;model_rows=$models.Count;female_models=$femaleModels.Count;female_roots=$femaleRoots.Count;loaded_config_packages=$sources.Count;load_errors=$loadErrors.Count}|ConvertTo-Json -Compress
}finally{
    if($null-ne(Get-Variable selectedProvider -ErrorAction SilentlyContinue)){$selectedProvider.Dispose()}elseif($null-ne$provider){$provider.Dispose()}
    if($null-ne(Get-Variable baseProvider -ErrorAction SilentlyContinue)){$baseProvider.Dispose()}
}
