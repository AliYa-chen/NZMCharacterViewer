# PC 端资源与渲染参数核对

核对日期：2026-11-09。只读本机 PC 安装的 `NZM` 磁盘资源及现有工程，已转换 194 张 PNG、36 个独立 LOD0 ActorX 网格、6 个 HDR 全景，导出人物页场景、材质及粒子参数。发布源文件位于 [PCResources](../PCResources/README.md)，离线工具位于 [PCAssetPipeline](../Tools/PCAssetPipeline/README.md)。**本机没有 UE，尚未执行编辑器导入；运行时 Content、Catalog 和画质配置未改。**

人物光照／后期优先的后续交接见 [PC 人物渲染还原交接](PC_CHARACTER_RENDERING_HANDOFF.md)。已进一步补取 10 包灯光模板、20 包肤／眼／发依赖；模板按真实导出序号核对，后期单列值／override 的已知与未知状态，并列出当前代码会覆盖来源参数的位置。该文档给出另一台 UE 机器需要实施的桥接导入、来源模式、shader／特效重建与验收步骤。

## 已确认与待确认

| 项目 | 结果 | 证据边界 |
| --- | --- | --- |
| PC 基础资源包 | `Content/Paks` 共 80 包、65,553,252,321 字节 | 包大小不能证明人物面数或贴图分辨率 |
| PC 更新资源包 | `Saved` 下共 57 包、2,074,749,428 字节 | 提取时还需核对实际挂载顺序与同名文件覆盖关系 |
| 资源目录索引 | 全部 137 包已通过主索引、路径哈希索引及完整目录索引 SHA1 核验，解析 837,533 条文件记录 | 完整资源列表已建立；文件记录含重复／更新版本，不等于独立资产数 |
| 压缩方式 | 包尾压缩表包含 Oodle／Zlib | 单个包可声明多种方法，不是逐资源解压验证 |
| PC 画质配置 | 本地保存为 High，常规 `sg.*Quality=3`，分辨率 2560×1440、100% | 用户配置，不能证明每个功能在某一帧实际生效 |
| PC 渲染参数 | 历史日志包含 `TrySetCvarQualityLevel Set Cvar` 记录 | 能证明游戏发出过设置记录，不能替代当前帧 GPU 验证 |
| 场景灯光与 HDR | 已导出 129 灯光、7 后期区域、6 HDR 及 5 粒子包 | 模板继承、运行时路由和最终画面仍有未确认项；HDR 是重投影后的全景 |
| PC 人物精度 | 40 个部件对应 36 个独立 PC 网格，29 项 LOD0 三角数更高、11 项相同 | 骨架／槽差异尚未通过 UE 导入与动画验证 |
| 高清纹理候选 | 100 张实际尺寸更高，其中 93 张通过当前材质到 PC 参数的引用链核验 | 93 对文件校验通过，尚未在 UE 应用或进行 UV／外观验证 |

定制包格式与上游 [CUE4Parse 的 FPakInfo 实现](https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse/UE4/Pak/Objects/FPakInfo.cs) 相符：`GAME_AssaultFireFuture` 使用上述 magic，索引长度和偏移的序列化顺序相反。上游 [EGame](https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse/UE4/Versions/EGame.cs) 提供该游戏的专用版本分支；这不是对本地游戏实际引擎版本的独立鉴定。格式适配不提供本地包的解密密钥。

用户随后提供的 [qiekn 解包文档](https://qiekn.notion.site/nzm-decrypt) 已通过浏览器读取。文档中的密钥与用户给出的一致，要求 FModel 使用 `AssaultFireFuture` 覆盖，并指出 UModel 需要特定 ACL 构建；它还链接了 [QuickBMS 包装脚本](https://github.com/qiekn/nzm-wiki/blob/main/scripts/decrypt.sh)。[UModel 维护者的游戏支持帖](https://www.gildor.org/smf/index.php?topic=9236.0) 同样要求专用覆盖与构建，较新的 ACL 实现有版本区别。文档可用于选择工具，但其成功案例不能替代对本地包的验证。

标准 AES-256 ECB 用用户给出的值测试两个基础包和一个更新包时，未得到有效挂载点，索引 SHA1 也不符；这不能直接判定字符串错误。上游另有 [AssaultFireFuture 专用解密实现](https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse/GameTypes/NMZ/Encryption/Aes/AssaultFireFutureAes.cs)，说明必须一并处理定制算法。未将用户密钥写入跟踪文件或报告。

应用该专用实现后，`pakchunk62optional`、`pakchunk0` 及更新包 `P_1.0.60.169.0_R_4583449_P.pak` 均解出有效挂载点，分别有 2、4594、47 个文件；**三个解密索引的 SHA1 均与包尾一致**。这一步已证明本地包可读取，不再把加密索引视作尚未解决的障碍。验证报告为 `Saved/PCAssetAnalysis/decrypt_probe.json`。本地源码适配及其 Apache 许可证保存在忽略目录 `Saved/PCAssetAnalysis/Tools/SourceRefMain`，没有作为项目运行依赖。

随后完成全部 137 包的目录验证：主索引、路径哈希索引与完整目录索引的 SHA1 全部相符，零失败；共 837,533 条文件记录已解码且位置／压缩块边界已校验。汇总为 `Saved/PCAssetAnalysis/Indexing/index_summary.json`。此阶段仅建立资源位置表，不把文件名匹配当成已解码或已替换的模型。

## 可复查的本地输出

`Saved/PCAssetAnalysis` 为忽略目录，保留安装路径、具体参数和技术证据，不分发原游戏包或完整日志。脚本仅从游戏目录读取，输出写入工程。

- `pak_inventory.json`：包大小、magic、版本、索引标志、偏移／长度及范围校验。
- `rendering_parameters.json`：允许清单内的画质配置、历史 CVar 设置、原文件行号与时间。
- `current_texture_sources.json`：现有角色贴图的 Source 尺寸与 ImportedSize 交叉核验。
- `current_mesh_sources.json`：40 个现有网格的原始导入名、骨架、槽名及已保存的 Registry 几何统计。

PC 主日志采用逐字节 XOR `0x73` 混淆；渲染检查脚本仅在内存恢复并筛选技术设置行，不保存完整解码日志。

```powershell
pwsh -File Scripts/inspect_pc_paks.ps1 -GameRoot 'Y:\网络游戏\逆战：未来\NZM'
pwsh -File Scripts/inspect_pc_rendering.ps1 -GameDirectory 'Y:\网络游戏\逆战：未来\NZM'
pwsh -File Scripts/inspect_texture_sources.ps1
```

## 已读取的 PC 光照、环境与特效参数

`rendering_parameters.json` 包含 41 条配置和 128 个独立 CVar 设置请求，来源为 `Saved/Config/WindowsClient` 和 `Saved/Logs/NZM.log`。日志请求集中在原时间戳 `2026.11.03-08.16.03:048` 至 `:100`、frame 454；日志时区没有独立判定，保留原值。这是 **2026-11-03 一次运行的历史记录**。

| 类别 | 日志中的具体请求 | 与当前工程的关系 |
| --- | --- | --- |
| 反射 | `r.SSR.Quality=2`、`r.SSR.HalfResSceneColor=1`、`r.SSR.Temporal=0` | 当前工程移动 SSR 质量 2、桌面 4，半分辨率为 0；不能把原 PC 配置直接等同更高画质 |
| AO | `r.AmbientOcclusionLevels=1`、`r.AmbientOcclusionMaxQuality=25`、`r.AmbientOcclusion.Method=0` | 当前代码 AO 最大质量为 100；实际接触阴影外观还受场景后期参数影响 |
| GI／PRT | `r.GIQualityHD=1`、`r.PRT.DirectionalSkyVisibility=1`、`r.PRTHDRangeMultiplier=2` | 属于原游戏实现线索，尚未取得计算方法或 PRT 数据 |
| 阴影 | `r.Shadow.CSM.MaxCascades=3`、`r.Shadow.MaxCSMResolution=1536`、`r.Shadow.MaxResolution=1536`、`r.ContactShadows=1` | 需要结合灯光组件参数；日志同时记录 `r.Shadow.IndividualLightPCSS=0` |
| Bloom／曝光／景深 | `r.BloomQuality=5`、`r.EyeAdaptationQuality=1`、`r.DepthOfFieldQuality=1`、`r.Tonemapper.Quality=2` | 这些是质量等级，缺少强度、曝光范围、LUT、焦距等场景输入 |
| 雾 | `r.Fog=1`、`r.VolumetricFog=1`、`GridPixelSize=32`、`GridSizeZ=32`、`HistoryWeight=.98`、`TemporalReprojection=1` | 开关不包含雾密度、颜色、距离和散射系数；XY 为每网格像素尺度，Z 为深度切片数，不是 32×32 的完整空间网格 |
| 透明／折射 | `r.RefractionQuality=2`、`r.TranslucencyLightingVolumeDim=24`、`r.TranslucencyLightingVolumeOuterDistance=10000` | 仍需原材质与场景数据 |
| 皮肤 | `r.SubsurfaceScattering=1`、`r.SSS.HalfRes=1`、`r.SSS.Quality=0` | 记录存在相应设置；不能推断每个角色材质使用该渲染路径 |
| 毛发 | `r.HairStrands.SkyAO.SampleCount=16`、`r.HairStrands.SkyLighting.SampleCount=16` | 不能仅凭全局开关证明当前角色使用 Groom／HairStrands；现有角色为发片输入 |
| 粒子 | `r.EmitterSpawnRateScale=1`、`r.ParticleLightQuality=2`、`r.ParticleLODBias=0` | 还记录原游戏的 CPU／GPU 预算覆盖参数；没有发射器、材质和时间曲线 |
| 纹理 | `r.Streaming.PoolSize=1000`、`r.Streaming.MipBias=0`、`r.MaxAnisotropy=2` | 驻留预算与各向异性等级不等于源纹理尺寸 |

另有异步资源请求列表指向以下数据表，可在解包后优先追踪。证据是同一解码日志第 5393 行、时间戳 `2026.11.03-08.16.03:100` 的 `RequestAsyncLoad called with both valid and null assets` 警告；**不能证明各资源成功加载**，也没有读取表内容：

- `/Game/DataTables/PostProcessDOFTable.PostProcessDOFTable`
- `/Game/DataTables/PostProcessEffect.PostProcessEffect`
- `/Game/DataTables/PostProcessRadialBlurTable.PostProcessRadialBlurTable`
- `/Game/Blueprints/TODSystem/NZGameClockConfigTable.NZGameClockConfigTable`
- `/Game/DataTables/SeasonOrnament/SeasonCollectionAvatarSceneConf.SeasonCollectionAvatarSceneConf`

PC 用户配置中的 `bUseHDRDisplayOutput=False` 指显示输出，不能用它判断场景是否使用 HDR 环境纹理。

## 现有工程的人物替换入口

现有工程为 UE 5.8.4，包含 5 名角色、9 套服装、40 个运行网格。`SourceAssets` 原始转换输入已清理，现有 `Content` 是可编辑工程资产。每个部件在 `ViewerCatalog` 中分别引用网格、材质、动画与动态配置。

| 显示内容／服装 | 当前路径标识 | 网格数 |
| --- | --- | ---: |
| Angel | `angel/angel` | 3 |
| Lucia | `angel/angellucia` | 5 |
| Angela | `angela/angela` | 5 |
| Molly | `molly/molly` | 4 |
| Nanami | `molly/mollynanami` | 4 |
| Naimei | `naimei/suit_a` | 4 |
| Bianca | `naimei/naimeibianca` | 4 |
| Queen | `queen/queen` | 5 |
| Queen masked | `queen/queen_masked` | 6 |

Naimei 使用 `body/head/hair/cloak` 部件 ID，其余多为数字 ID。后续必须按部件原始虚拟路径与实际骨架匹配，不能只按角色目录名替换。

现有导入器 `Scripts/import_assets.py` 支持骨骼 FBX 和纹理，保留法线／切线，设置八权重及高精度 skin weights；不支持把原游戏 cooked UE4 `.uasset` 直接放入 UE5 工程。`refresh_assets.py` 会复用已有路径的资产，也不能用于强制升级已存在的网格／贴图。

**不要用只包含一个 PC 角色的清单直接运行全量导入器**：它会重建整个 Catalog，可能丢失其他服装及已有的动画、材质、场景绑定。应先在独立候选目录导入，确认 LOD0、材质槽、骨架名称／层级／参考姿势、权重、透明边缘，再备份 Catalog 并定点替换已匹配的引用。Angela 头部已有专门的参考姿势修正动画，换骨架时需重新核对。

当前代码未发现固定降低网格 LOD 或 `MaxTextureSize` 的设置；纹理低档确实会设置 `r.Streaming.MipBias=1/1.5`、纹理池 256/160 MB，高档为 MipBias 0、768 MB。贴图模糊可能还涉及来源尺寸与运行时驻留 mip，需要分别核实。

已离线核验 `Content/Characters/Textures` 下的全部 244 个资产：受 `Source:TextureSource` 结构边界约束的 `SizeX/SizeY:IntProperty` 与独立 `ImportedSize:IntPoint` 全部相符，零失败。最大导入源尺寸为 **1024×1024**；1024² 112 张、512² 90 张、256² 27 张、128² 1 张、64² 1 张、1² 13 张。这里不是用缩略图推测尺寸；Source 的 mip 数也不等于运行时生成或驻留的 mip 数。

40 个现有网格也全部完成 AssetRegistry 表读取，保留原始 FBX 名与 Skeleton 引用，零失败。已保存统计合计 685,044 顶点、759,870 三角，40 项均标记一个 LOD。例如 Angela 头部为 `SK_Char_Angela_UI_Head_Mesh.fbx`，8,124 顶点、13,880 三角、113 骨骼；Angel 身体保留原名拼写 `SK_Char_Angle_UI_Body_Mesh.fbx`，37,467 顶点、40,404 三角。这些是 Registry 元数据，未将其称为已直接解码的 LOD0 缓冲。当前网格本身不能仅凭外观认定为低模，贴图来源最高 1K 则已明确确认。

## 场景与特效迁移需要的进一步数据

已按现有人物页／服装场景依赖提取相关来源，后续导入仍需继续核对：

1. 人物网格：LOD0 顶点／三角形、各 LOD、材质槽、UV、权重、骨架及动画绑定。
2. 纹理：最高可用 mip 尺寸与编码、基色／法线／透明度／packed 通道语义、材质实例继承与开关。
3. 场景：地图实例及蓝图组件模板继承后的灯光变换、颜色、强度单位、衰减、阴影、照明通道；天光、HDR cubemap、反射捕获。
4. 后期：实际启用的 override、曝光、白平衡、色调映射、LUT、Bloom、AO、景深，以及相机／Sequence 的动态覆盖。
5. 特效：Niagara／Cascade 系统、发射器、材质、纹理、用户参数、挂点和演出时序。`r.ParticleLightQuality` 等开关不包含这些效果本体。

本项目已有 `UViewerEnvironment` 的天光、灯光、场景几何和后期字段，以及现有环境导入链。PC 端的私有 `r.PRT.*`、`r.GIQualityHD` 等名字只能作为实现线索；没有对应算法、预计算数据或引擎支持，不能仅复制 CVar 名称就视为迁移完成。未在本轮选取的渲染日志中发现 Lumen／Enlighten 设置，也不能据此证明整个游戏未使用某项技术。

ActorX 已逐项核对原解码点坐标、面数、骨骼数和每条非零权重／索引；194 张 PNG 重新解码通过，且尺寸等于最高可用 mip。6 个环境源均为 128×128／面的 FloatRGBA cubemap，输出为 443×221 Radiance 全景，涉及重投影和 RGBE 量化。

`PCResources/upgrade_manifest.json` 只列入 93 个引用链核验通过的高清纹理。`Scripts/import_pc_textures.py` 默认审计，`--validate-files` 可在普通 Python 中验证全部目标／源哈希、PNG 尺寸及当前 UE5.8 Source 元数据；已在本机通过 93／93。`apply` 才会在 UE 中备份并定点重导入现有纹理，保持当前设置和 metadata，不重建材质或 Catalog；该编辑器路径尚未实测。重导入会保留导入器管理的源文件路径，PNG 随仓库保留。

完整资源清单和 SHA256 检查通过；本机临时 Python 3.12.7 下的工程静态检查通过。后续仍需用 `verify_imported_assets.py` 核验结构与骨架、`audit_content.py` 核验依赖，并在真实渲染与目标设备上比较。本次不宣称通过 UE 导入、编译、视觉或设备验证。
