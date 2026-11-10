# 可复现的离线导出 Runtime

要求 **Windows、PowerShell 7.6+、.NET 10+**。在仓库根目录的新 PowerShell 会话中执行。工具只处理指定目录中的资源文件，不使用进程枚举、内存读取、调试、注入、游戏启动或反作弊接口。

本页是按需重新导出的流程，不是查看器构建或运行依赖。完整高清导出在全部转换与最终核验通过后从工作树清理，原结果保留在 [PCResources 历史快照](https://github.com/AliYa-chen/NZMCharacterViewer/tree/03152adb7ce937649f890455d6650f346e82acc1/PCResources)。本页从游戏安装重新导出到 `Saved`，不要求恢复旧源目录；其他工具需要历史源文件时，见[上级说明](../README.md)中的可选恢复命令。

## 准备与核验

```powershell
& ./Tools/PCAssetPipeline/Runtime/Prepare-Runtime.ps1
& ./Tools/PCAssetPipeline/Runtime/Prepare-Runtime.ps1 -Mode Verify -Offline
& ./Tools/PCAssetPipeline/Runtime/Prepare-Runtime.ps1 -Mode Audit -Offline
```

`Prepare` 按 `runtime.lock.json` 下载并验证 **117 个 NuGet 归档**和固定 OodleUE 归档，然后检查实际使用的 **62 个托管 DLL、Skia 原生 DLL 和 Oodle DLL**。每项均固定版本、下载地址与 SHA256。NuGet 的安装／构建脚本不会执行。

默认缓存为仓库下 `Saved/PCAssetAnalysis/ToolRuntime`。原始 `.nupkg`、展开后的 `.nuspec`、许可文件及生成的 `runtime_manifest.json` 都留在此缓存中。仓库保存脚本、锁文件与许可说明。没有全局 SDK 安装步骤。

`Verify` 和 `Audit` 都不下载或修改缓存；`Audit` 另列包许可元数据的数量分布。缺失文件、归档或 DLL 校验不符、包身份不符及锁版本不同都会失败。导入 DLL 时也会重新校验实际加载文件。

可从已有归档离线构建一个新缓存：

```powershell
& ./Tools/PCAssetPipeline/Runtime/Prepare-Runtime.ps1 `
  -Offline -SeedDirectory $existingArchiveDirectory
```

`SeedDirectory` 中需有锁文件指定名称的 `.nupkg` 和 `oodle-win-x64.zip`。`-CacheDirectory` 可覆盖缓存位置，但必须仍在当前仓库的 `Saved` 子目录中。缓存目录应专用于此工具。

## 元数据、PNG、JSON 与网格

```powershell
$packages = Read-Host 'NZM 的 Content/Paks 目录'
$targets = @(
  '/Game/Avatar/Character/Angela/CommonRes/Textures_HD/T_Char_Angela_Suit_B_Head_C_HD'
  '/Game/Avatar/Character/Angela/CommonRes/Mesh/SK_Char_Angela_UI_Head_Mesh'
)

& ./Tools/PCAssetPipeline/Runtime/Export-Assets.ps1 `
  -InputDirectory $packages -ObjectPaths $targets -Mode Metadata

& ./Tools/PCAssetPipeline/Runtime/Export-Assets.ps1 `
  -InputDirectory $packages -ObjectPaths $targets `
  -Mode JsonAndTextures -ExportMeshes

& ./Tools/PCAssetPipeline/Runtime/Validate-Exports.ps1
```

默认输出为 `Saved/PCAssetAnalysis/PCExports`，可用 `-OutputDirectory` 覆盖。`Metadata` 只解析与记录；`Json` 导出对象属性；`JsonAndTextures` 另导出最高可用 mip 的纹理。`-ExportMeshes` 导出最高 LOD 的 ActorX `.psk/.pskx`。输出目录必须在输入目录之外。

导出报告记录网格 LOD／骨骼／权重计数、纹理最高可用 mip、格式／sRGB／法线标记，以及 provider 选中的 `.uasset/.uexp/.ubulk/.uptnl` 来源包和 bulk header。传入 `Content/Paks` 时仅包含基础包；provider 的选中顺序会记录，但不代表已经与某次游戏运行的更新覆盖顺序核验一致。

法线走 DesktopMobile 解码，BC5 的 Z 通道由解码器恢复；PNG 导出不翻转绿色通道，不经过 glTF 材质转换。原纹理设置及材质参数仍需结合 JSON 判断。浮点 cubemap 会转换为 Radiance HDR 全景，包含投影重采样和 RGBE 量化。

当前固定版本的 glTF writer 使用四权重。此入口使用 ActorX 保留源权重。`Validate-Exports.ps1` 将 ActorX 的每个顶点与每条非零权重／点索引／骨索引同源 DTO 逐项比较，并重新解码 PNG、核对最高可用 mip 尺寸。它不验证 UE 导入、材质外观、动画或目标设备运行。

`Export-Assets.ps1` 和 `Import-Runtime.ps1` 使用本地缓存，不主动下载文件；缺少原生库时会停止。更新锁或加载过其他版本 CUE4Parse 后，应使用新的 PowerShell 会话。

## 来源与许可

库固定为 CUE4Parse／Conversion `1.2.2.202611`，源提交 `e76778c10ed25860e11ec488750753224d067d87`。见 [第三方说明](THIRD_PARTY.md)、[Apache 2.0 原文](LICENSE-CUE4Parse.txt)、[上游 NOTICE](NOTICE-CUE4Parse.txt)，以及锁文件中每个包的原始许可元数据。
