[CmdletBinding()]
param(
 [string]$AnalysisRoot=(Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))) 'Saved/PCAssetAnalysis')
)
$ErrorActionPreference='Stop'
$analysis=(Get-Item -LiteralPath $AnalysisRoot).FullName
$currentPath=Join-Path $analysis 'current_texture_sources.json'
$variantsPath=Join-Path $analysis 'Indexing/current_texture_pc_variants.json'
$metadataPath=Join-Path $analysis 'PCExports/export_report.json'
$current=Get-Content -LiteralPath $currentPath -Raw | ConvertFrom-Json
$variants=Get-Content -LiteralPath $variantsPath -Raw | ConvertFrom-Json
$metadata=Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json

function ConvertTo-RequestedPath([string]$Path){
 $value=$Path.Replace('\','/')
 if($value -match '^(\.\./)+NZM/Content/') {return ($value -replace '^(\.\./)+NZM/Content/','/Game/')}
 if($value -match '^(\.\./)+Engine/Content/') {return ($value -replace '^(\.\./)+Engine/Content/','/Engine/')}
 return $value
}
function Compare-Dimensions([int]$CurrentWidth,[int]$CurrentHeight,[int]$Width,[int]$Height){
 if($Width -le 0 -or $Height -le 0){return 'missing'}
 if($Width -eq $CurrentWidth -and $Height -eq $CurrentHeight){return 'same'}
 if($Width -ge $CurrentWidth -and $Height -ge $CurrentHeight){return 'actualhigher'}
 if($Width -le $CurrentWidth -and $Height -le $CurrentHeight){return 'lower'}
 return 'ambiguous'
}
function Read-PngDimensions([string]$Path){
 $stream=[IO.File]::OpenRead($Path)
 try{
  $header=New-Object byte[] 24
  if($stream.Read($header,0,24) -ne 24){throw 'Short PNG header.'}
  if([Convert]::ToHexString($header[0..7]) -ne '89504E470D0A1A0A' -or [Text.Encoding]::ASCII.GetString($header,12,4) -ne 'IHDR'){throw 'Invalid PNG signature or IHDR.'}
  $w=[byte[]]$header[16..19];[Array]::Reverse($w)
  $h=[byte[]]$header[20..23];[Array]::Reverse($h)
  return [pscustomobject]@{width=[BitConverter]::ToUInt32($w,0);height=[BitConverter]::ToUInt32($h,0)}
 }finally{$stream.Dispose()}
}
$byCurrent=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
foreach($asset in $current.assets){if($byCurrent.ContainsKey($asset.file)){throw 'Duplicate current texture file.'};$byCurrent.Add($asset.file,$asset)}
$byRequested=[Collections.Generic.Dictionary[string,Collections.Generic.List[object]]]::new([StringComparer]::OrdinalIgnoreCase)
foreach($record in @($metadata.records | Where-Object class -eq 'Texture2D')){
 if(-not $byRequested.ContainsKey($record.requested_path)){$byRequested.Add($record.requested_path,[Collections.Generic.List[object]]::new())}
 $byRequested[$record.requested_path].Add($record)
}
$byObject=[Collections.Generic.Dictionary[string,Collections.Generic.List[object]]]::new([StringComparer]::OrdinalIgnoreCase)
foreach($export in @($metadata.exports | Where-Object kind -eq 'Textures')){
 if(-not $byObject.ContainsKey($export.object_path)){$byObject.Add($export.object_path,[Collections.Generic.List[object]]::new())}
 $byObject[$export.object_path].Add($export)
}

$rows=[Collections.Generic.List[object]]::new()
foreach($asset in $variants.assets){
 if(-not $byCurrent.ContainsKey($asset.current)){throw ('Current source evidence missing: '+$asset.current)}
 $original=$byCurrent[$asset.current]
 $currentWidth=[int]$original.source.width;$currentHeight=[int]$original.source.height
 $enriched=[Collections.Generic.List[object]]::new()
 foreach($candidate in $asset.candidates){
  $requested=ConvertTo-RequestedPath $candidate.virtual_package
  $measurement=$null;$measurementStatus='missing_metadata';$loadedRecord=$null
  if($byRequested.ContainsKey($requested)){
   $records=@($byRequested[$requested] | Where-Object status -eq 'loaded')
   if($records.Count -eq 1){
    $loadedRecord=$records[0]
    $texture=$loadedRecord.texture
    $index=$texture.highest_available_mip_index
    $mips=@($texture.mips)
    if($null -ne $index -and [int]$index -ge 0 -and [int]$index -lt $mips.Count){
     $mip=$mips[[int]$index]
     $exports=@()
     if($byObject.ContainsKey($loadedRecord.object_path)){
      $exports=@($byObject[$loadedRecord.object_path] | Where-Object success)
     }
     $pngFiles=@($exports | ForEach-Object files | Where-Object {$_ -like '*.png'})
     $pngProofs=@(foreach($pngFile in $pngFiles){
      if(-not [IO.File]::Exists($pngFile)){[pscustomobject]@{file=$pngFile;status='missing_file'};continue}
      $pngSize=Read-PngDimensions $pngFile
      [pscustomobject]@{file=$pngFile;status='read_png_ihdr';width=$pngSize.width;height=$pngSize.height;matches_available_mip=([int]$pngSize.width -eq [int]$mip.width -and [int]$pngSize.height -eq [int]$mip.height)}
     })
     $measurement=[pscustomobject]@{
      width=[int]$mip.width;height=[int]$mip.height
      highest_available_mip_index=[int]$index
      first_mip_to_serialize=$texture.first_mip_to_serialize
      declared_texture_width=$texture.width;declared_texture_height=$texture.height
      pixel_format=$texture.pixel_format
      is_normal_map=$texture.is_normal_map
      srgb=$texture.srgb
      compression_settings=$texture.compression_settings
      bulk_flags=$mip.bulk_flags;bulk_size=$mip.bulk_size
      dimension_comparison=(Compare-Dimensions $currentWidth $currentHeight ([int]$mip.width) ([int]$mip.height))
      width_ratio=([double]$mip.width/$currentWidth)
      height_ratio=([double]$mip.height/$currentHeight)
      pixel_count_ratio=([double]$mip.width*[double]$mip.height/([double]$currentWidth*$currentHeight))
      decoded_pngs=$pngProofs
     }
     $measurementStatus='available_mip_measured'
    }else{$measurementStatus='no_available_mip_index'}
   }elseif($records.Count -gt 1){$measurementStatus='ambiguous_metadata_records'}
  }
  $enriched.Add([pscustomobject]@{
   virtual_package=$candidate.virtual_package;requested_path=$requested;match_basis=$candidate.match_basis
   indexed_archives=@($candidate.versions | ForEach-Object archive)
   source_measurement_status=$measurementStatus
   source_measurement=$measurement
   selected_package_files=if($null -ne $loadedRecord){$loadedRecord.selected_package_files}else{$null}
   patch_priority_verified=$false;replacement_verified=$false
  })
 }
 $proposed=$null;$status='missing';$reason='No indexed candidate.'
 if($enriched.Count -gt 1){$status='ambiguous';$reason='Multiple full virtual package candidates; none selected.'}
 elseif($enriched.Count -eq 1){
  $candidate=$enriched[0]
  $exact=$candidate.match_basis -in @('exact_leaf','exact_leaf_full_index')
  $hd=$candidate.match_basis -eq 'explicit_pc_suffix_HD'
  $eligible=if($asset.kind -eq 'texture_vmgen'){$exact}else{$exact -or $hd}
  if(-not $eligible){$status='ambiguous';$reason='Candidate was derived from a suffix/collision heuristic outside the proposal rule.'}
  else{
   $proposed=$candidate
   if($null -eq $candidate.source_measurement){$status='missing';$reason='Unique eligible candidate lacks an available-mip measurement.'}
   else{$status=$candidate.source_measurement.dimension_comparison;$reason='Compared current imported source dimensions with the proposed PC cooked highest available mip.'}
  }
 }
 $rows.Add([pscustomobject]@{
  current_file=$asset.current;kind=$asset.kind;normalized_source_name=$asset.source_name
  current_source_width=$currentWidth;current_source_height=$currentHeight
  comparison=$status;reason=$reason
  proposed_source=$proposed
  candidate_count=$enriched.Count;candidates=@($enriched)
  replacement_verified=$false;patch_priority_verified=$false
  uv_equivalence_verified=$false
  generated_atlas_or_crop_equivalence_verified=if($asset.kind -eq 'texture_vmgen'){$false}else{$null}
 })
}
if($rows.Count -ne 244 -or $rows.Count -ne $current.assets.Count){throw 'Comparison does not cover all 244 current textures.'}
$counts=[ordered]@{current_texture_count=$rows.Count;actualhigher=0;same=0;lower=0;missing=0;ambiguous=0;proposed_source_count=0;replacement_verified_count=0}
foreach($row in $rows){$counts[$row.comparison]++;if($null -ne $row.proposed_source){$counts.proposed_source_count++}}
if(($counts.actualhigher+$counts.same+$counts.lower+$counts.missing+$counts.ambiguous) -ne $rows.Count){throw 'Classification totals do not reconcile.'}
$byKind=@($rows | Group-Object kind | ForEach-Object {
 $group=$_.Group
 [pscustomobject]@{kind=$_.Name;current_texture_count=$group.Count;actualhigher=@($group | Where-Object comparison -eq 'actualhigher').Count;same=@($group | Where-Object comparison -eq 'same').Count;lower=@($group | Where-Object comparison -eq 'lower').Count;missing=@($group | Where-Object comparison -eq 'missing').Count;ambiguous=@($group | Where-Object comparison -eq 'ambiguous').Count;proposed_source_count=@($group | Where-Object {$null -ne $_.proposed_source}).Count}
})
$sourceEvidence=@(foreach($path in @($currentPath,$variantsPath,$metadataPath)){[pscustomobject]@{file=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash}})
$pngChecks=@($rows | ForEach-Object candidates | ForEach-Object source_measurement | ForEach-Object decoded_pngs)
$pngFailures=@($pngChecks | Where-Object {$_.status -ne 'read_png_ihdr' -or -not $_.matches_available_mip})
$validation=[ordered]@{current_rows=244;unique_current_files=@($rows.current_file | Sort-Object -Unique).Count;classification_totals_reconcile=$true;png_ihdr_checks=$pngChecks.Count;png_ihdr_failures=$pngFailures.Count;all_proposed_sources_have_measurements=(@($rows | Where-Object {$null -ne $_.proposed_source -and $null -eq $_.proposed_source.source_measurement}).Count -eq 0)}
$result=[ordered]@{
 schema_version=1;generated_at_utc=[DateTime]::UtcNow.ToString('o')
 method='Join all 244 editable-project source dimensions to indexed full virtual texture package candidates and actual PC Texture2D records. Dimensions use mips[highest_available_mip_index]; available exported PNG IHDR dimensions are crosschecked.'
 proposal_policy='A sole full virtual package may be proposed when it is an exact leaf match, or (direct textures only) an explicit _HD match. Multiple full paths remain ambiguous; _Desktop and collision-suffix-only candidates are not automatically proposed.'
 comparison_policy='actualhigher/lower require both dimensions to be respectively no smaller/no larger with at least one strict change; same requires both equal; mixed dimensions are ambiguous. Missing source/measurement is missing.'
 metadata_scope=[ordered]@{input_root=$metadata.input_root;mounted_archives=$metadata.mounted_archives;priority_scope=$metadata.priority_scope;metadata_generated_at_utc=$metadata.generated_at_utc;texture_record_count=@($metadata.records | Where-Object class -eq 'Texture2D').Count}
 limitations=@('Dimension increases establish available resolution only; they do not verify texture semantics, visual quality, material compatibility, UV layout, or successful project replacement.','Provider metadata covers the stated base Content/Paks input tree. Saved update archives remain indexed candidates but are not proven live mount priority or replacement sources.','All proposed sources and replacements remain unverified. Ambiguous full paths are retained without selecting one.','Exact VMGen leaf matches do not establish atlas/crop or UV equivalence; generated texture substitutions require separate material/UV evidence.','Current dimensions describe imported source metadata; they are not current resident GPU mip dimensions.')
 summary=$counts;summary_by_kind=$byKind;validation=$validation;source_evidence=$sourceEvidence;assets=@($rows)
}
$out=Join-Path $analysis 'texture_upgrade_comparison.json'
[IO.File]::WriteAllText($out,($result | ConvertTo-Json -Depth 20 -Compress),[Text.UTF8Encoding]::new($false))
$counts | ConvertTo-Json -Compress
$byKind | ConvertTo-Json -Compress
Write-Output ('Report: '+$out)
