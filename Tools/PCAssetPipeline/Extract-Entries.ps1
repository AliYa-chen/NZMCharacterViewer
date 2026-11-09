<# Local, offline extraction helper. Input: JSON array of {archive,entry} locators.
Preserves each source archive in a separate output directory. Never chooses
between base/update versions. Requires locally supplied CUE4Parse cipher DLL.
Oodle is optional and must be explicitly supplied for Oodle-compressed files.
Does not launch games or access any other process.
#>
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$GameRoot,
 [Parameter(Mandatory=$true)][string]$LocatorJson,
 [Parameter(Mandatory=$true)][string]$CipherAssembly,
 [string]$OodleDll,
 [string]$OutputDirectory=(Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'Saved/PCAssetAnalysis/Extracted')
)
$ErrorActionPreference='Stop'
$game=(Get-Item -LiteralPath $GameRoot).FullName.TrimEnd([char[]]'\/')
$output=[IO.Path]::GetFullPath($OutputDirectory)
if($output.Equals($game,[StringComparison]::OrdinalIgnoreCase) -or $output.StartsWith($game+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Output must be outside GameRoot.'}
$cipher=(Get-Item -LiteralPath $CipherAssembly).FullName
Add-Type -Path $cipher
$source=@'
using System;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using CUE4Parse.UE4.VirtualFileSystem;
using CUE4Parse.GameTypes.NMZ.Encryption.Aes;
namespace NZMOfflineExtraction {
public sealed class ExtractionProof {
 public string output_sha1 {get;set;}
 public string header_sha1 {get;set;}
 public string header_hash_basis {get;set;}
}
public static class Extractor {
 [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
 delegate long OodleDecompress(IntPtr input,long inputLength,IntPtr output,long outputLength,int fuzzSafe,int checkCRC,int verbosity,IntPtr baseBuffer,long baseLength,IntPtr callback,IntPtr user,IntPtr memory,long memoryLength,int phase);
 static IntPtr native=IntPtr.Zero; static OodleDecompress oodle;
 static byte[] Exact(BinaryReader r,int n){var data=r.ReadBytes(n);if(data.Length!=n)throw new EndOfStreamException();return data;}
 static byte[] Decode(byte[] input,int inputLength,int outputLength,string method,string dll){
  if(method.Equals("Zlib",StringComparison.OrdinalIgnoreCase)){
   using(var compressed=new MemoryStream(input,0,inputLength,false))
   using(var z=new ZLibStream(compressed,CompressionMode.Decompress))
   using(var output=new MemoryStream()){z.CopyTo(output);var data=output.ToArray();if(data.Length!=outputLength)throw new InvalidDataException("Zlib output length mismatch.");return data;}
  }
  if(!method.Equals("oodle",StringComparison.OrdinalIgnoreCase))throw new NotSupportedException("Compression method: "+method);
  if(oodle==null){if(String.IsNullOrWhiteSpace(dll))throw new NotSupportedException("Oodle DLL was not supplied.");native=NativeLibrary.Load(Path.GetFullPath(dll));oodle=Marshal.GetDelegateForFunctionPointer<OodleDecompress>(NativeLibrary.GetExport(native,"OodleLZ_Decompress"));}
  var bytes=new byte[outputLength];var inputHandle=GCHandle.Alloc(input,GCHandleType.Pinned);var outputHandle=GCHandle.Alloc(bytes,GCHandleType.Pinned);
  try{long n=oodle(inputHandle.AddrOfPinnedObject(),inputLength,outputHandle.AddrOfPinnedObject(),outputLength,1,0,0,IntPtr.Zero,0,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,0,3);if(n!=outputLength)throw new InvalidDataException("Oodle output length mismatch: "+n);return bytes;}
  finally{outputHandle.Free();inputHandle.Free();}
 }
 public static ExtractionProof Extract(string archive,long offset,long compressedSize,long size,int methodIndex,string method,bool encrypted,int structSize,uint blockSize,long[] starts,long[] ends,string outputPath,string oodleDll){
  if(size<0 || size>int.MaxValue || compressedSize<0 || compressedSize>int.MaxValue)throw new InvalidDataException("Unsupported entry size.");
  using(var f=File.Open(archive,FileMode.Open,FileAccess.Read,FileShare.ReadWrite))
  using(var r=new BinaryReader(f))
  using(var storedHash=IncrementalHash.CreateHash(HashAlgorithmName.SHA1))
  using(var decodedBlocksHash=IncrementalHash.CreateHash(HashAlgorithmName.SHA1))
  using(var paddedBlocksHash=IncrementalHash.CreateHash(HashAlgorithmName.SHA1)){
   if(offset<0 || offset>f.Length-structSize)throw new InvalidDataException("Entry outside archive.");f.Position=offset;
   // NZM's duplicated on-disk header stores compressed size before its offset.
   long headerCompressed=r.ReadInt64();r.ReadInt64();long headerSize=r.ReadInt64();int headerMethod=r.ReadInt32();var hash=Exact(r,20);
   if(headerCompressed!=compressedSize || headerSize!=size || headerMethod!=methodIndex)throw new InvalidDataException("Stored entry header disagrees with index locator.");
   if(methodIndex!=0){int blocks=r.ReadInt32();if(blocks!=starts.Length)throw new InvalidDataException("Header block count mismatch.");for(int i=0;i<blocks;i++){long a=r.ReadInt64()+offset,b=r.ReadInt64()+offset;if(a!=starts[i] || b!=ends[i])throw new InvalidDataException("Header compression blocks disagree with locator.");}}
   byte flags=r.ReadByte();uint storedBlockSize=r.ReadUInt32();if(((flags&1)!=0)!=encrypted || f.Position!=offset+structSize)throw new InvalidDataException("Stored flags/header size disagree with locator.");
   var output=new byte[(int)size];
   if(methodIndex==0){long readSize=encrypted?(size+15)&~15L:size;if(readSize>int.MaxValue || f.Position>f.Length-readSize)throw new InvalidDataException("Payload outside archive.");var data=Exact(r,(int)readSize);storedHash.AppendData(data);if(encrypted)data=AssaultFireFutureAes.AssaultFireFutureDecrypt(data,0,data.Length,false,new ProbeReader(),null);paddedBlocksHash.AppendData(data);decodedBlocksHash.AppendData(data,0,(int)size);Array.Copy(data,output,output.Length);}
   else{
    if(starts.Length==0 || starts.Length!=ends.Length || blockSize==0)throw new InvalidDataException("Invalid compressed block metadata.");
    int outputPosition=0;
    for(int i=0;i<starts.Length;i++){long length=ends[i]-starts[i],readSize=encrypted?(length+15)&~15L:length;if(length<0 || readSize>int.MaxValue || starts[i]<0 || starts[i]>f.Length-readSize)throw new InvalidDataException("Block outside archive.");f.Position=starts[i];var data=Exact(r,(int)readSize);storedHash.AppendData(data);if(encrypted)data=AssaultFireFutureAes.AssaultFireFutureDecrypt(data,0,data.Length,false,new ProbeReader(),null);paddedBlocksHash.AppendData(data);decodedBlocksHash.AppendData(data,0,(int)length);int target=(int)Math.Min(blockSize,size-outputPosition);var block=Decode(data,(int)length,target,method,oodleDll);Array.Copy(block,0,output,outputPosition,block.Length);outputPosition+=block.Length;}
    if(outputPosition!=output.Length)throw new InvalidDataException("Final output size mismatch.");
   }
   string actual;using(var sha=SHA1.Create())actual=BitConverter.ToString(sha.ComputeHash(output)).Replace("-","");
   string expected=BitConverter.ToString(hash).Replace("-","");
   string stored=BitConverter.ToString(storedHash.GetHashAndReset()).Replace("-","");
   string decoded=BitConverter.ToString(decodedBlocksHash.GetHashAndReset()).Replace("-","");
   string padded=BitConverter.ToString(paddedBlocksHash.GetHashAndReset()).Replace("-","");
   string basis=expected==stored?"stored_payload_including_alignment":expected==decoded?"decrypted_blocks_without_alignment":expected==padded?"decrypted_blocks_including_alignment":expected==actual?"decoded_file":null;
   if(basis==null)throw new InvalidDataException("Pak payload SHA1 mismatch.");
   Directory.CreateDirectory(Path.GetDirectoryName(outputPath));File.WriteAllBytes(outputPath,output);return new ExtractionProof{output_sha1=actual,header_sha1=expected,header_hash_basis=basis};
  }
 }
}
}
'@
$references=@([IO.Directory]::GetFiles((Join-Path $PSHOME 'ref'),'*.dll'))+@($cipher)
Add-Type -TypeDefinition $source -ReferencedAssemblies $references
if($OodleDll){$OodleDll=(Get-Item -LiteralPath $OodleDll).FullName}
$locators=@(Get-Content -LiteralPath $LocatorJson -Raw | ConvertFrom-Json)
$reports=@()
foreach($locator in $locators){
 try{
  $archivePath=[IO.Path]::GetFullPath((Join-Path $game $locator.archive))
  if(-not $archivePath.StartsWith($game+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Archive locator escapes GameRoot.'}
  $entry=$locator.entry
  $relative=($entry.path -replace '^(\.\./)+','').Replace('/','\')
  if([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)'){throw 'Virtual file path escapes output.'}
  $archiveFolder=$locator.archive.Replace('/','__').Replace('\','__')
  $archiveOutput=[IO.Path]::GetFullPath((Join-Path $output $archiveFolder))
  $outputPath=[IO.Path]::GetFullPath((Join-Path $archiveOutput $relative))
  if(-not $outputPath.StartsWith($archiveOutput+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Output file escapes archive folder.'}
  $starts=[long[]]@($entry.blocks | ForEach-Object start)
  $ends=[long[]]@($entry.blocks | ForEach-Object end)
  $proof=[NZMOfflineExtraction.Extractor]::Extract($archivePath,[long]$entry.offset,[long]$entry.compressed_size,[long]$entry.uncompressed_size,[int]$entry.compression_method_index,[string]$entry.compression_method,[bool]$entry.encrypted,[int]$entry.struct_size,[uint32]$entry.compression_block_size,$starts,$ends,$outputPath,$OodleDll)
  $reports += [pscustomobject]@{archive=$locator.archive;virtual_path=$entry.path;status='extracted_sha1_verified';file=$outputPath;bytes=$entry.uncompressed_size;output_sha1=$proof.output_sha1;header_sha1=$proof.header_sha1;header_hash_basis=$proof.header_hash_basis}
 }catch{$reports += [pscustomobject]@{archive=$locator.archive;virtual_path=$locator.entry.path;status='error';error=$_.Exception.Message}}
}
$null=[IO.Directory]::CreateDirectory($output)
[IO.File]::WriteAllText((Join-Path $output 'extraction_report.json'),($reports | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
$failed=@($reports | Where-Object status -eq 'error').Count
Write-Output ('Extracted '+($reports.Count-$failed)+' files with verified SHA1; failures: '+$failed)
if($failed -gt 0){exit 1}
