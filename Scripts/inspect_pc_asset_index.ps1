<#
.SYNOPSIS
Lists on-disk NZM PC Pak indexes and file locations without extracting assets.
.DESCRIPTION
Requires an independently supplied, locally built CUE4Parse NZM cipher adapter.
No key or third-party cipher implementation is embedded in this script.
Only the specified NZM installation and project analysis output are accessed.
Does not enumerate processes, read process memory, launch games, or use a debugger.
Format references (CUE4Parse, Apache-2.0):
https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse/UE4/Pak/PakFileReader.cs
https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse/UE4/Pak/Objects/FPakEntry.cs
.EXAMPLE
pwsh -File Scripts/inspect_pc_asset_index.ps1 -GameRoot 'Y:\games\NZM' -CipherAssembly 'Saved/PCAssetAnalysis/Tools/SourceRefMain/AssaultFireFutureProbe2.dll'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$GameRoot,
    [Parameter(Mandatory = $true)][string]$CipherAssembly,
    [string]$InventoryPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'Saved/PCAssetAnalysis/pak_inventory.json'),
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'Saved/PCAssetAnalysis/Indexing'),
    [int]$ArchiveLimit = 0
)
$ErrorActionPreference = 'Stop'
$resolvedRoot = (Get-Item -LiteralPath $GameRoot).FullName.TrimEnd([char[]]'\/')
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
if ($resolvedOutput.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedOutput.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must be outside the read-only game installation.'
}
$resolvedCipher = (Get-Item -LiteralPath $CipherAssembly).FullName
Add-Type -Path $resolvedCipher
$null = [IO.Directory]::CreateDirectory($resolvedOutput)
$parserSource = @'
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Security.Cryptography;
using CUE4Parse.UE4.VirtualFileSystem;
using CUE4Parse.GameTypes.NMZ.Encryption.Aes;

namespace NZMOfflineIndex {
public sealed class Block {
    public long start {get;set;}
    public long end {get;set;}
}
public sealed class Entry {
    public string path {get;set;}
    public int encoded_location {get;set;}
    public long offset {get;set;}
    public long compressed_size {get;set;}
    public long uncompressed_size {get;set;}
    public int compression_method_index {get;set;}
    public string compression_method {get;set;}
    public bool encrypted {get;set;}
    public bool deleted {get;set;}
    public uint compression_block_size {get;set;}
    public int struct_size {get;set;}
    public string sha1_from_header {get;set;}
    public List<Block> blocks {get;set;} = new List<Block>();
}
public sealed class Archive {
    public string archive {get;set;}
    public string mount_point {get;set;}
    public int declared_file_count {get;set;}
    public int enumerated_file_count {get;set;}
    public int non_encoded_count {get;set;}
    public int directory_count {get;set;}
    public bool primary_sha1_matches {get;set;}
    public bool directory_sha1_matches {get;set;}
    public bool path_hash_sha1_matches {get;set;}
    public long primary_index_offset {get;set;}
    public long primary_index_size {get;set;}
    public long directory_index_offset {get;set;}
    public long directory_index_size {get;set;}
    public List<Entry> entries {get;set;} = new List<Entry>();
}
public static class Parser {
    static byte[] Exact(BinaryReader r,int count) {
        if(count<0) throw new InvalidDataException("Negative byte count.");
        var b=r.ReadBytes(count); if(b.Length!=count) throw new EndOfStreamException(); return b;
    }
    static string FString(BinaryReader r) {
        int count=r.ReadInt32();
        if(count==0) return "";
        if(count==int.MinValue || Math.Abs(count)>1048576) throw new InvalidDataException("Invalid FString length.");
        bool wide=count<0; int bytes=checked(Math.Abs(count)*(wide?2:1));
        var value=Exact(r,bytes);
        if(value[value.Length-1]!=0 || (wide && value[value.Length-2]!=0)) throw new InvalidDataException("FString lacks terminator.");
        return (wide?Encoding.Unicode:Encoding.UTF8).GetString(value,0,value.Length-(wide?2:1));
    }
    static int Count(BinaryReader r) {
        var n=r.ReadInt32();if(n<0 || n>20000000)throw new InvalidDataException("Invalid collection count.");return n;
    }
    static bool Bool(BinaryReader r) {
        int n=r.ReadInt32();if(n!=0 && n!=1)throw new InvalidDataException("Invalid serialized Boolean.");return n==1;
    }
    static byte[] ReadUnit(BinaryReader r,long offset,long size,bool encrypted) {
        if(offset<0 || size<=0 || size>int.MaxValue || offset>r.BaseStream.Length-size)throw new InvalidDataException("Index outside archive bounds.");
        r.BaseStream.Position=offset;var b=Exact(r,(int)size);
        if(encrypted) b=AssaultFireFutureAes.AssaultFireFutureDecrypt(b,0,b.Length,true,new ProbeReader(),null);
        return b;
    }
    static bool HashMatches(byte[] value,byte[] expected) {
        using(var sha=SHA1.Create())return BitConverter.ToString(sha.ComputeHash(value))==BitConverter.ToString(expected);
    }
    static string Method(int index,string[] methods) {
        if(index<0 || index>=methods.Length)throw new InvalidDataException("Unknown compression method index.");return methods[index];
    }
    static Entry ReadFull(BinaryReader r,string[] methods) {
        long begin=r.BaseStream.Position;
        var e=new Entry();e.offset=r.ReadInt64();e.compressed_size=r.ReadInt64();e.uncompressed_size=r.ReadInt64();
        e.compression_method_index=r.ReadInt32();e.compression_method=Method(e.compression_method_index,methods);
        e.sha1_from_header=BitConverter.ToString(Exact(r,20)).Replace("-","");
        if(e.compression_method_index!=0) {
            int n=Count(r);for(int i=0;i<n;i++)e.blocks.Add(new Block{start=checked(e.offset+r.ReadInt64()),end=checked(e.offset+r.ReadInt64())});
        }
        byte flags=r.ReadByte();e.encrypted=(flags&1)!=0;e.deleted=(flags&2)!=0;e.compression_block_size=r.ReadUInt32();
        e.struct_size=checked((int)(r.BaseStream.Position-begin));return e;
    }
    static Entry ReadEncoded(BinaryReader r,int location,string[] methods) {
        if(location<0 || location>r.BaseStream.Length-4)throw new InvalidDataException("Encoded entry location outside table.");
        r.BaseStream.Position=location;uint bits=r.ReadUInt32();
        uint sizeCode=bits&0x3fu;uint blockSize=sizeCode==0x3fu?r.ReadUInt32():sizeCode<<11;
        var e=new Entry();e.encoded_location=location;
        e.compression_method_index=(int)((bits>>23)&0x3f);e.compression_method=Method(e.compression_method_index,methods);
        e.offset=(bits&0x80000000u)!=0?r.ReadUInt32():r.ReadInt64();
        e.uncompressed_size=(bits&0x40000000u)!=0?r.ReadUInt32():r.ReadInt64();
        e.compressed_size=e.compression_method_index==0?e.uncompressed_size:((bits&0x20000000u)!=0?r.ReadUInt32():r.ReadInt64());
        e.encrypted=(bits&0x400000u)!=0;
        int blocks=(int)((bits>>6)&0xffff);
        e.compression_block_size=blocks==1?checked((uint)e.uncompressed_size):(blocks>0?blockSize:0);
        e.struct_size=53+(e.compression_method_index==0?0:4+blocks*16);
        long blockOffset=checked(e.offset+e.struct_size);
        if(blocks==1 && !e.encrypted)e.blocks.Add(new Block{start=blockOffset,end=checked(blockOffset+e.compressed_size)});
        else for(int i=0;i<blocks;i++) {
            uint length=r.ReadUInt32();e.blocks.Add(new Block{start=blockOffset,end=checked(blockOffset+length)});
            blockOffset=checked(blockOffset+(e.encrypted?((long)length+15)&~15L:length));
        }
        return e;
    }
    static void ValidateEntry(Entry e,long fileLength) {
        if(e.offset<0 || e.compressed_size<0 || e.uncompressed_size<0 || e.offset>fileLength-e.struct_size || e.compressed_size>fileLength-e.offset-e.struct_size)
            throw new InvalidDataException("Entry outside archive: "+e.path);
        foreach(var b in e.blocks)if(b.start<e.offset || b.end<b.start || b.end>fileLength)throw new InvalidDataException("Block outside archive: "+e.path);
    }
    public static Archive Read(string archivePath) {
        using(var f=File.Open(archivePath,FileMode.Open,FileAccess.Read,FileShare.ReadWrite))
        using(var file=new BinaryReader(f)) {
            if(f.Length<221)throw new InvalidDataException("Truncated footer.");f.Position=f.Length-221;
            Exact(file,16);byte encrypted=file.ReadByte();uint magic=file.ReadUInt32();int version=file.ReadInt32();
            if(magic!=0x4F6FAE86 || version!=10 || encrypted>1)throw new InvalidDataException("Unsupported NZM footer.");
            long primarySize=file.ReadInt64(),primaryOffset=file.ReadInt64();var primaryHash=Exact(file,20);
            var methods=new string[6];methods[0]="None";for(int i=1;i<=5;i++)methods[i]=Encoding.ASCII.GetString(Exact(file,32)).TrimEnd('\0');
            var result=new Archive{archive=archivePath,primary_index_offset=primaryOffset,primary_index_size=primarySize};
            var primary=ReadUnit(file,primaryOffset,primarySize,encrypted==1);
            result.primary_sha1_matches=HashMatches(primary,primaryHash);if(!result.primary_sha1_matches)throw new InvalidDataException("Primary index SHA1 mismatch.");
            using(var p=new BinaryReader(new MemoryStream(primary,false))) {
                result.mount_point=FString(p);result.declared_file_count=Count(p);p.ReadUInt64();
                if(!Bool(p))throw new InvalidDataException("No path hash index.");
                long hashOffset=p.ReadInt64(),hashSize=p.ReadInt64();var hashHash=Exact(p,20);
                if(!Bool(p))throw new InvalidDataException("No full directory index.");
                long dirOffset=p.ReadInt64(),dirSize=p.ReadInt64();var dirHash=Exact(p,20);
                result.directory_index_offset=dirOffset;result.directory_index_size=dirSize;
                var encoded=Exact(p,Count(p));int fullCount=Count(p);result.non_encoded_count=fullCount;
                var full=new List<Entry>();for(int i=0;i<fullCount;i++)full.Add(ReadFull(p,methods));
                var hashData=ReadUnit(file,hashOffset,hashSize,encrypted==1);
                result.path_hash_sha1_matches=HashMatches(hashData,hashHash);if(!result.path_hash_sha1_matches)throw new InvalidDataException("Path hash index SHA1 mismatch.");
                var dirData=ReadUnit(file,dirOffset,dirSize,encrypted==1);
                result.directory_sha1_matches=HashMatches(dirData,dirHash);if(!result.directory_sha1_matches)throw new InvalidDataException("Directory index SHA1 mismatch.");
                using(var dirs=new BinaryReader(new MemoryStream(dirData,false)))
                using(var enc=new BinaryReader(new MemoryStream(encoded,false))) {
                    int directories=Count(dirs);result.directory_count=directories;
                    for(int i=0;i<directories;i++) {
                        string directory=FString(dirs);if(result.mount_point.EndsWith("/") && directory.StartsWith("/"))directory=directory.Substring(1);
                        int n=Count(dirs);for(int j=0;j<n;j++) {
                            string name=FString(dirs);int location=dirs.ReadInt32();if(location==int.MinValue)continue;
                            Entry entry;
                            if(location>=0)entry=ReadEncoded(enc,location,methods);
                            else {int fullIndex=checked(-location-1);if(fullIndex>=full.Count)throw new InvalidDataException("Invalid full entry reference.");entry=full[fullIndex];entry.encoded_location=location;}
                            entry.path=(result.mount_point+directory+name).Replace('\\','/');
                            ValidateEntry(entry,f.Length);result.entries.Add(entry);
                        }
                    }
                }
            }
            result.enumerated_file_count=result.entries.Count;
            if(result.enumerated_file_count!=result.declared_file_count)throw new InvalidDataException("Directory file count differs from primary index.");
            return result;
        }
    }
}
}
'@
$parserPath = Join-Path $resolvedOutput 'NZMOfflineIndex.cs'
[IO.File]::WriteAllText($parserPath, $parserSource, [Text.UTF8Encoding]::new($false))
$references = @([IO.Directory]::GetFiles((Join-Path $PSHOME 'ref'), '*.dll')) + @($resolvedCipher)
Add-Type -TypeDefinition $parserSource -ReferencedAssemblies $references
$inventory = Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
if (-not $resolvedRoot.Equals($inventory.game_root.TrimEnd([char[]]'\/'), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Inventory game_root does not match GameRoot.'
}
$archives = @($inventory.archives | Where-Object valid)
if ($ArchiveLimit -gt 0) { $archives = @($archives | Select-Object -First $ArchiveLimit) }
$records = @()
$totalFiles = 0
foreach ($archive in $archives) {
    $archivePath = Join-Path $resolvedRoot $archive.relative_path
    $resultName = $archive.relative_path.Replace('/', '__').Replace('\', '__') + '.json'
    try {
        $result = [NZMOfflineIndex.Parser]::Read($archivePath)
        $jsonPath = Join-Path $resolvedOutput $resultName
        [IO.File]::WriteAllText($jsonPath, ($result | ConvertTo-Json -Depth 10 -Compress), [Text.UTF8Encoding]::new($false))
        $records += [pscustomobject]@{ archive = $archive.relative_path; status = 'verified'; file_count = $result.enumerated_file_count; mount_point = $result.mount_point; primary_sha1_matches = $result.primary_sha1_matches; directory_sha1_matches = $result.directory_sha1_matches; path_hash_sha1_matches = $result.path_hash_sha1_matches; report = $jsonPath }
        $totalFiles += $result.enumerated_file_count
    }
    catch {
        $records += [pscustomobject]@{ archive = $archive.relative_path; status = 'error'; error = $_.Exception.Message }
    }
}
$failed = @($records | Where-Object status -eq 'error').Count
$summary = [ordered]@{ schema_version = 1; generated_at_utc = [DateTime]::UtcNow.ToString('o'); game_root = $resolvedRoot; archive_count = $records.Count; verified_archive_count = $records.Count - $failed; failed_archive_count = $failed; enumerated_file_count = $totalFiles; extraction_performed = $false; model_quality_assessed = $false; archives = @($records) }
[IO.File]::WriteAllText((Join-Path $resolvedOutput 'index_summary.json'), ($summary | ConvertTo-Json -Depth 7), [Text.UTF8Encoding]::new($false))
Write-Output ('Indexes: {0} verified archives, {1} files, {2} failed archives. Reports: {3}' -f ($records.Count - $failed), $totalFiles, $failed, $resolvedOutput)
if ($failed -gt 0) { exit 1 }
