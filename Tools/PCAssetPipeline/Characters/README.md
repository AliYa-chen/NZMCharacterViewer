# 女性角色配置、库存与资源收集

工具只读取 NZM 安装文件及本地索引，不调用进程、内存、调试、注入、游戏启动或反作弊接口。依赖 PowerShell 7.6／.NET 10、Python 3.10+ 和相邻 Runtime 的固定版本缓存。

先按[上级说明](../README.md)生成 `Saved/PCAssetAnalysis/Indexing/index_summary.json`。本次安装有137个已验证档案；每个 `report` 指向包含 `entries[]` 的索引 JSON。跨机器时旧报告的绝对路径失效，工具按当前索引目录中的同名 JSON 查找。

在仓库根目录运行：

```powershell
$gameRoot = Read-Host 'NZM 目录（其下包含 Content/Paks 和 Saved）'
./Tools/PCAssetPipeline/Runtime/Prepare-Runtime.ps1
python ./Tools/PCAssetPipeline/Characters/Inventory-Characters.py
./Tools/PCAssetPipeline/Characters/Resolve-Outfits.ps1 -GameDirectory $gameRoot
./Tools/PCAssetPipeline/Characters/Review-SourceOnlyCandidates.ps1 -GameDirectory $gameRoot
python ./Tools/PCAssetPipeline/Characters/Inventory-Characters.py `
  --female-config ./Saved/PCAssetAnalysis/ExpandedCharacters/outfit_config_evidence.json
./Tools/PCAssetPipeline/Characters/Resolve-Outfits.ps1 -GameDirectory $gameRoot
```

第一次 Resolve 提供明确女性根供补充类型审查，第二次 Inventory 补充女性根与此前导出项的对照报告；最后一次 Resolve 将最终库存、审查摘要及哈希纳入证据。所有分析结果默认位于 `Saved/PCAssetAnalysis/ExpandedCharacters`。

- `inventory.json`：全部角色目录、完整虚拟路径和基础／更新包出现位置；区分 UI、1P、3P、CutScene、预览、物理代理等命名候选。`PCResources/mesh_comparison.json` 仅用于对照此前已导出项。
- `ConfigEvidence/base_content_paks`、`ConfigEvidence/provider_selected_all_archives`：基础包和包含更新包的 provider 分别选中的7种角色配置表、所需 DA 及已复核的补充网格 JSON。`outfit_config_evidence.json` 保留来源归档、哈希、模型行、显式性别、全部默认视图路线、实际部件配置和自定义替换规则。
- `ReviewCandidates/review_summary.json`：从全部已验证索引独立寻找未知角色根及不以 `SK_` 命名的候选，记录实际解码类型。文件名、性别相关名称和目录后缀均不等于可选角色或完整服装证据。
- `female_mesh_plan.json`：`mesh_paths` 仅包含本安装 provider 中存在的包；缺失引用保留在 `unavailable_mesh_references`，原始并集保留在 `all_candidate_mesh_paths`。默认路线、自定义替换、库存候选与 source-only 资源分别可查。包存在性不等于导出已成功。

本次基础模型表92行、更新 provider 选中表84行，并集92行。27行具有明确 `ModeConfig.Gender=Female`，含 bot、别名及服装变体，归属12个资源根；这不表示27名现役可选角色。40行缺少明确性别：38行引用的 DA 在两个 provider 文件集中都不存在，1行 `PrimaryAssetName=None`，1行 Queen_Naked 的 DA 没有显式 Gender。Queen_Naked 作为已确认 Queen 根内的源资源候选保留。基础表独有的比安卡、艾玛等行仍保留，并注明来源版本。

补充审查加载166个包、4,822个对象且无加载失败，确认 Default_Female_00 基础女模和 Angel 的 `Wristband_Mesh`。此外，艾玛基础表的自定义替换直接引用 `/Game/Avatar/Base/Pendant_Base_Neak`：实际为共享骨骼挂件底座，使用 male-named 骨架，不算额外女性角色。三条路径见 `reviewed_mesh_packages.json`；Resolve 每次重新解码并校验类型、骨架，以及 Default_Female 的5个明确材质引用。`-ReviewedMeshFile` 可指定另一份显式复核清单。

最终计划共347个候选／配置路径：309个安装内存在的包需要收集，38个缺失配置引用单列。其中 Default_Female 仅是 source-only 女模，不为缺失的 DA 或当前可选性补造证据。UI／CutScene／Preview／1P／3P／Master／饰件均保留，不表示全部可同时显示。`DefaultPartItems` 是序列化默认列表，未完整解决模板继承、染色、运行时显示条件及商业服装 ID。

兔耳资源属于独立 Bunny／芭比（模型ID10002000056）的 Suit_C 路线，其兔耳部件配置明确命名。Naimei（10002000045）默认 UI 指向 Suit_A 身体／披风／刀鞘／头花和 CommonRes 头发；Naimei_Suit_C 的神乐铃与 CutScene 候选不能仅按后缀认定为兔女郎或商业服装名。

收集安装内存在的全部计划包：

```powershell
./Tools/PCAssetPipeline/Characters/Collect-CharacterResources.ps1 `
  -GameDirectory $gameRoot `
  -PlanJson ./Saved/PCAssetAnalysis/ExpandedCharacters/female_mesh_plan.json `
  -MaterialBudget 2000 -MeshBatchSize 12 -NoExistingReuse -Resume
```

`-NoExistingReuse` 使用本次基础＋更新包统一来源。`-Resume` 复用相同输入树、同一输出目录中已有成功报告和通过验证的导出；安装内容变化时更换输出目录重新收集。显式 `mesh_paths` 允许 `Wristband_Mesh`、`Pendant_Base_Neak` 等无 `SK_` 前缀的包。单独的 `-MeshPathsFile` 接受 JSON 路径数组；`-PlanOnly` 仅列网格阶段。Resolve 可用 `-InventoryFile` 指定库存；缺少库存时只能覆盖配置和显式复核路径。

资源输出在 `Resources`：网格 JSON／ActorX、逐槽材质引用、父材质、显式纹理及解码 PNG／HDR，汇总为 `character_resource_manifest.json`；`collection_progress.json` 记录阶段。每批默认12个网格。材质预算或导出失败会写入 `failures` 并返回非零。

验证将 ActorX 点、三角形、骨骼及全部非零权重／索引与解码源数据比较，并将 PNG 尺寸与最高可用 mip 比较。HDR 检查签名、尺寸和完整 RLE 像素数据，不比较 cubemap 重投影前后的逐像素数值。JSON 保留网格内嵌衣物对象、原骨架和计划。待机、动画和显示路线以配置证据保存；动画转换、UE5.8 导入、原 shader 重建与运行画面验收仍需在目标 UE 机器完成。

数据验证全部通过后，可从仓库根目录发布并检查：

```powershell
python ./Scripts/publish_pc_characters.py `
  --collection ./Saved/PCAssetAnalysis/ExpandedCharacters/Resources `
  --evidence ./Saved/PCAssetAnalysis/ExpandedCharacters
python ./Scripts/validate_pc_resources.py
```

发布器只接受失败数为零、未复用旧版 PCResources 的收集结果。输出为 `PCResources/Characters`；按新导出文件的 SHA256 与既有发布文件比较后去重，保留新的来源记录。配置／审查／验证报告中的实际文件引用转换为仓库相对路径；未分发的原始 Pak 索引标为来源证明，不作为运行依赖。
