# 女性角色与服装源资源

2026-11-09 补充。已按本机 **137 个基础／更新档案**中的配置、完整目录库存和实际解码类型，收集 **309 个骨骼网格包、629 个材质／父材质包、1101 个纹理包**。纹理实际输出 1099 张 PNG 和 2 个 HDR；相同字节在发布目录中去重。2039 个资源全部成功，原始导出的 1410 个网格／图像文件均有数据校验记录。

这些是供后续 UE 导入的源文件。**查看器的 `Content`、Catalog 和界面仍是原来的 5 名角色／9 套服装；新增资源尚未接入运行时。** 普通编译不会自动导入本目录，也不代表原游戏画面已经还原。

## 角色和目录列表

表中数量按源网格包计，包括 UI、第一／第三人称、CutScene、Preview、Master 和附件；目录分组不是可选服装数，不能把所有部件同时显示。

| 资源根 | 配置说明／主要分支 | 网格包 |
| --- | --- | ---: |
| Angel | 安琪儿、永恒之火；CommonRes、Angel_Lucia_A、手环 | 38 |
| Angela | 安吉拉晚宴特工；Angela_Suit_B、CommonRes、场景道具 | 23 |
| Bella | 贝拉、女团；Bella_Pop_C、Bella_Suit_D、CommonRes | 21 |
| Bunny | 芭比／兔女郎；Bunny_Suit_C、CommonRes | 16 |
| Emma | 艾玛；Emma_Suit_C、CommonRes | 7 |
| Lilith | 莉莉丝；Lilith_Suit_C、CommonRes | 11 |
| Molly | 茉莉、七实；Molly_Nanami_B、Molly_Suit_C、CommonRes | 28 |
| Naimei | 奈美、比安卡；Naimei_Suit_A、Naimei_Suit_C、Naimei_Bianca_A、CommonRes、场景道具 | 57 |
| Queen | 精绝女王；Queen_Insect_S、CommonRes | 42 |
| Shirley | 雪莉杨；Shirley_Suit_B、CommonRes、场景道具 | 21 |
| Tina | 黑暗天使；Tina_Drone_B、CommonRes、场景道具 | 15 |
| Widow | 黑寡妇；Widow_Suit_C、CommonRes | 16 |
| Default_Female_00 | 实际解码确认的女性基础模型；未确认对应可选角色 | 1 |
| Shared | 配置引用的共享附件／底座，含 Pendant_Base_Neak；不算新增女性角色 | 13 |

完整包名、文件位置和分组见 [catalog.json](catalog.json)。每个网格的实际槽绑定在 [character_resource_manifest.json](character_resource_manifest.json) 的 `mesh_material_edges`；父材质和纹理引用在 `material_dependency_edges`。文件位置可能指向上一级已存在的 `Meshes`、`Textures` 或 `Metadata`：只有新导出结果与旧文件的 SHA256 完全相同才复用，未按同名路径混用旧版资产。

Bunny 是配置明确的独立角色，兔耳也是明确部件。Naimei 默认 UI 组合指向 Suit_A 身体、披风、刀鞘、头花及 CommonRes 头发；Suit_C 的神乐铃和 CutScene 资源已保留，但目录后缀不能证明商业服装名称或默认 UI 组合。

## 范围与缺失记录

基础模型表 92 行，含补丁的离线 provider 选中表 84 行；保留两者并集，避免漏掉基础表里的比安卡和艾玛。27 行有明确 `ModeConfig.Gender=Female`，其中含 bot、别名和变体，归属上述 12 个角色资源根，**不是 27 名可选角色**。

另有 40 行缺少明确性别：38 行找不到其声明的 DA、1 行资产名为空、1 行 Queen_Naked 的 DA 没有显式 Gender。独立审查还读取了 166 个非标准命名／未知角色候选包中的 4822 个对象，读取失败 0，补出了女性基础模型、手环及共享挂件底座。其他未知名称目前没有可补的女性骨骼网格证据。

347 个配置／库存候选中，**309 个安装内存在，38 个引用在本机全部档案中都不存在，已跳过**。发布计划只包含实际存在的网格，不为缺失项提交空资源或导入占位。本集合覆盖这份安装能够读取的模型及显式材质／纹理依赖，不声称覆盖未安装内容或在线现役角色列表。原始配置表的历史字段按来源保留。

## 文件与校验

- [Configuration/outfit_config_evidence.json](Configuration/outfit_config_evidence.json)：模型 ID、基础／补丁表版本、显式性别、默认视图路线、可替换部件和来源归档。
- `Configuration/ConfigEvidence/`：实际序列化配置；`Configuration/ReviewCandidates/`：补充类型和骨架审查证据。完整 Pak 索引仅保留原始校验摘要，不随仓库分发。
- `Metadata/`、`Meshes/`、`Textures/`：新增 JSON、ActorX 和图像，包含成功导出的内嵌材质节点、衣物等子对象；引用到上一级的文件按原位置使用。
- `Validation/`：导出报告及逐文件验证；清单保留基础／补丁归档、偏移、provider 选择顺序。该顺序未与原游戏运行时的挂载顺序逐项对照。

从仓库根目录执行，无需游戏或 UE：

```bash
python3 Scripts/validate_pc_resources.py
```

检查包括全部文件 SHA256、计划／清单／目录一致性、依赖引用、证据哈希与验证覆盖。ActorX 的顶点、三角形、骨骼和每条非零权重／索引已和解码源数据对照；PNG 已解码并核对最高可用 mip 尺寸；HDR 已检查完整 RLE 像素数据，不宣称与原 cubemap 逐像素相等。

## 在另一台机器接入

先按配置的 `DefaultPartItems`、视图路线和替换条件选择组合，再进行 ActorX→FBX 转换与 UE 导入。保留坐标、骨架、全部权重和材质槽；不要直接按现有查看器槽号套用。纹理应按原 `sRGB`／法线设置和实际参数绑定导入。

动画与骨架引用、内嵌衣物参数保留在 JSON 中；本次没有补成可播放的完整动画包，也没有重建原 shader、动态材质或服装选择界面。需要在目标 UE5.8.4 机器继续完成转换、动画与附件适配、运行时 Catalog 和画面对照。光照／后期接入顺序见 [渲染交接](../../Docs/PC_CHARACTER_RENDERING_HANDOFF.md)。

重新提取的完整步骤与工具见 [Characters 工具说明](../../Tools/PCAssetPipeline/Characters/README.md)。游戏资源权利说明见 [第三方资源说明](../../THIRD_PARTY_NOTICES.md)。
