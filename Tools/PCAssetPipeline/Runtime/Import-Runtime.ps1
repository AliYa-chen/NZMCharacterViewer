# Dot-source in a fresh PowerShell 7.6+ process. Only loads locked local DLLs.
[CmdletBinding()]
param([string]$CacheDirectory)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$savedRoot = Join-Path $repositoryRoot 'Saved'
if (-not $CacheDirectory) { $CacheDirectory = Join-Path $savedRoot 'PCAssetAnalysis/ToolRuntime' }
$script:NZMRuntimeCacheRoot = [IO.Path]::GetFullPath($CacheDirectory).TrimEnd('\','/')
if (-not $NZMRuntimeCacheRoot.StartsWith($savedRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'CacheDirectory must be a subdirectory of this repository''s Saved directory.'
}
$runtimeManifestPath = Join-Path $NZMRuntimeCacheRoot 'runtime_manifest.json'
$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
$lockPath = Join-Path $PSScriptRoot 'runtime.lock.json'
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
$lockHash = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($runtimeManifest.status -ne 'locked_assets_verified' -or $runtimeManifest.lock_sha256 -ne $lockHash) {
    throw 'Run Prepare-Runtime.ps1 for this lock before importing.'
}
function Get-LockedRuntimePath($Asset) {
    $assetPath = [IO.Path]::GetFullPath((Join-Path $NZMRuntimeCacheRoot $Asset.path))
    if (-not $assetPath.StartsWith($NZMRuntimeCacheRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.File]::Exists($assetPath) -or
        (Get-FileHash -LiteralPath $assetPath -Algorithm SHA256).Hash -ine $Asset.sha256) {
        throw "Runtime DLL failed its locked path/checksum check: $($Asset.path)"
    }
    return $assetPath
}
$managedPaths = @($lock.managed_assets | ForEach-Object { Get-LockedRuntimePath $_ })
$nativePaths = @($lock.native_assets | ForEach-Object { Get-LockedRuntimePath $_ })
foreach ($externalNative in $lock.external_native) {
    [void](Get-LockedRuntimePath ([pscustomobject]@{path=$externalNative.output;sha256=$externalNative.output_sha256}))
}
if (-not ('NZMOffline.RuntimeResolver' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.Loader;
using System.Runtime.InteropServices;
namespace NZMOffline {
    public static class RuntimeResolver {
        private static readonly Dictionary<string,string> managed = new(StringComparer.OrdinalIgnoreCase);
        private static readonly Dictionary<string,string> native = new(StringComparer.OrdinalIgnoreCase);
        private static bool registered;
        public static void Register(string[] managedPaths, string[] nativePaths) {
            foreach (string path in managedPaths) {
                AssemblyName name = AssemblyName.GetAssemblyName(path);
                if (!managed.TryGetValue(name.Name!, out string old) ||
                    AssemblyName.GetAssemblyName(old).Version < name.Version) managed[name.Name!] = Path.GetFullPath(path);
            }
            foreach (string path in nativePaths) {
                native[Path.GetFileName(path)] = Path.GetFullPath(path);
                native[Path.GetFileNameWithoutExtension(path)] = Path.GetFullPath(path);
            }
            if (registered) return;
            registered = true;
            AssemblyLoadContext.Default.Resolving += Resolve;
            AppDomain.CurrentDomain.AssemblyLoad += (_, args) => RegisterNative(args.LoadedAssembly);
        }
        private static Assembly Resolve(AssemblyLoadContext context, AssemblyName name) {
            return managed.TryGetValue(name.Name!, out string path) ? context.LoadFromAssemblyPath(path) : null;
        }
        private static void RegisterNative(Assembly assembly) {
            if (assembly.IsDynamic || !managed.ContainsKey(assembly.GetName().Name!)) return;
            try {
                NativeLibrary.SetDllImportResolver(assembly, (name, asm, search) => {
                    if (native.TryGetValue(name, out string path) || native.TryGetValue(Path.GetFileName(name), out path))
                        return NativeLibrary.Load(path);
                    return IntPtr.Zero;
                });
            } catch (InvalidOperationException) { }
        }
        public static Assembly Load(string name) {
            if (!managed.TryGetValue(name, out string path)) throw new FileNotFoundException(name);
            return AssemblyLoadContext.Default.LoadFromAssemblyPath(path);
        }
    }
}
'@
}
[NZMOffline.RuntimeResolver]::Register([string[]]$managedPaths, [string[]]$nativePaths)
$script:CueParserAssembly = [NZMOffline.RuntimeResolver]::Load('CUE4Parse')
$script:CueConversionAssembly = [NZMOffline.RuntimeResolver]::Load('CUE4Parse-Conversion')
[ordered]@{parser=$CueParserAssembly.FullName;conversion=$CueConversionAssembly.FullName;lock_sha256=$lockHash;local_dll_loading_only=$true}
