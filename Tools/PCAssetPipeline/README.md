# PC 资源离线工具

本次实际使用的文件分析、解包、转换和验证入口。Windows 侧要求 PowerShell 7.6／.NET 10；只读取指定 NZM 磁盘目录，输出写入工程 `Saved/PCAssetAnalysis`。不需要启动游戏、附加进程或读取内存。查看器不依赖这些工具。

## 已包含的工具

| 入口 | 用途 |
| --- | --- |
| `../../Scripts/inspect_pc_paks.ps1` | 读取 Pak footer、索引标志及范围 |
| `../../Scripts/inspect_pc_asset_index.ps1` | 校验三种索引 SHA1，列出文件与压缩块位置 |
| `../../Scripts/inspect_pc_rendering.ps1` | 仅提取允许清单内的画质配置与历史渲染设置日志 |
| `../../Scripts/inspect_texture_sources.ps1` | 读取现有 UE5.8 纹理 Source／ImportedSize |
| `Cipher/Build-CipherAdapter.ps1` | 从附带上游源码构建离线文件解密适配 DLL |
| `Extract-Entries.ps1` | 根据已选择的文件位置提取 None／Zlib／Oodle 条目并验证头部哈希 |
| `Runtime/` | SDK-free 固定版本依赖准备、属性 JSON／PNG／HDR／ActorX 导出与逐项验证 |
| `Analysis/Compare-Textures.ps1` | 以实际可用 mip 比较当前纹理与 PC 候选 |
| `Analysis/Verify-TextureBindings.ps1` | 核对当前材质的实际 Texture 引用与 PC 材质参数链 |
| `Analysis/Find-SceneTargets.ps1` | 汇总人物页灯光、背景和数据表候选 |
| `Lighting/` | 补取外部灯光模板，按真实导出索引核对身份并保留逐属性继承来源 |
| `Shading/` | 提取有依据的肤／眼／发依赖并生成可还原程度报告 |
| `Characters/` | 全角色目录库存、女性配置与非标准命名审查、网格／材质／纹理依赖收集 |
| `../../Scripts/publish_pc_characters.py` | 将已验证的角色集合发布为可移植清单，按实际字节去重并保留来源证据 |
| `../../Scripts/build_pc_postprocess_profiles.py` | 构建稳定、可检查的后期字段值／override 档案 |
| `../../Scripts/build_pc_quality_reference.py` | 将允许清单内的历史画质报告整理为可发布的相对路径数据 |
| `../../Scripts/validate_pc_resources.py` | 无游戏／引擎的发布资源完整性校验 |
| `../../Scripts/import_pc_textures.py` | 独立文件检查及 UE5.8 定点纹理重导入；编辑器应用未实测 |

`Runtime` 的依赖安装脚本固定本次使用的 117 个 NuGet 包、原生库归档和实际加载 DLL 的校验值。仓库附带源码、入口脚本、锁和许可文件；下载的完整工具二进制与依赖缓存按锁重建到 `Saved`，不会占用仓库或修改全局 SDK。详细步骤见 [Runtime/README.md](Runtime/README.md)。

## 复现索引与提取

在仓库根目录的新 PowerShell 会话中执行；cipher 的最小 reader 与完整 Runtime 库使用同名命名空间，分别在新会话运行，不混合加载。

```powershell
$gameRoot = Read-Host 'NZM 目录（其下包含 Content/Paks）'
& ./Scripts/inspect_pc_paks.ps1 -GameRoot $gameRoot
& ./Scripts/inspect_texture_sources.ps1
& ./Scripts/inspect_pc_rendering.ps1 -GameDirectory $gameRoot
$cipher = & ./Tools/PCAssetPipeline/Cipher/Build-CipherAdapter.ps1
& ./Scripts/inspect_pc_asset_index.ps1 -GameRoot $gameRoot -CipherAssembly $cipher
```

索引不会自动选择基础包／更新包优先级。文件提取器需要一个 JSON 数组，每项为 `{ "archive": "Content/Paks/…pak", "entry": { … } }`；`entry` 直接取自已验证索引，保留完整虚拟路径。Oodle 条目另传本地 DLL：

```powershell
& ./Tools/PCAssetPipeline/Extract-Entries.ps1 `
  -GameRoot $gameRoot -LocatorJson $selectedEntriesJson -CipherAssembly $cipher `
  -OodleDll ./Saved/PCAssetAnalysis/ToolRuntime/Native/oodle-data-shared.dll
```

`Analysis` 中的比较工具还需要本次生成的候选索引和本地导出报告，参数默认指向 `Saved/PCAssetAnalysis`；它们不会根据模糊命名自动确认纹理兼容性。已审阅的候选与最终证据同时发布在 [PCResources](../../PCResources/README.md)，使用发布源文件时无需重跑解包。

## 来源与本次验证

`Cipher/Upstream/AssaultFireFutureAes.cs`、`NetEaseAes.cs` 来自 [CUE4Parse](https://github.com/FabianFG/CUE4Parse)，保留实际使用的源码、Apache-2.0 许可及 NOTICE。构建脚本仅提取所需 `Td0…Td4` 查找表，并增加最小 API marker；未把用户输入的密钥写入配置。完整 Runtime 使用固定 `e76778c10ed25860e11ec488750753224d067d87` 提交的官方 NuGet 构建。

本次重新从跟踪源码构建 cipher 后，基础包单包索引烟测通过（4594 条，三类 SHA1 一致）。完整原分析 137 包、837,533 条文件记录通过。Runtime 从空缓存用已下载归档离线重建并 Verify／Audit 通过，跟踪版导出脚本读取 Angela 2048² 纹理通过；原批 194 PNG、36 ActorX 的验证全部通过。None、Zlib、Oodle 三种文件提取样本均检查了存储头 SHA1。

解析工具通过不等于 UE 导入、动画、原 shader 或画面已复原。游戏资源与工具依赖的许可分别见 [第三方资源说明](../../THIRD_PARTY_NOTICES.md) 及 [Runtime 第三方来源](Runtime/THIRD_PARTY.md)。

另一台 UE 机器的实施顺序和实际验收要求见 [PC 人物渲染还原交接](../../Docs/PC_CHARACTER_RENDERING_HANDOFF.md)。
