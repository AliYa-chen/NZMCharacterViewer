# PC 人物光照、后期与特效还原交接

更新：2026-11-09。目标是在现有 UE **5.8.4** 人物查看器中建立有来源证据的 PC 人物页渲染候选，重点是人物受光、环境反射、曝光／色调／Bloom、皮肤／眼球／毛发响应及背景特效。先固定人物和镜头恢复这些表现，再处理模型／贴图升级，避免同时改变多项输入而无法判断原因。

**这是一份提取数据与实施交接文档。当前机器没有 Unreal Engine；以下编译、编辑器导入、shader 集成和画面验收必须在接手机器完成。** 普通编译只构建现有代码，不会把 `PCResources` 的 JSON 自动变成环境、材质或特效资产。当前 `Content` 和 Catalog 保留原状态。

代码核对基线：`84503b60865057a9c3c4b481d7369548b5dbad21`。数据来自 PC 安装基础 `Content/Paks` 的 80 个包；137 个基础／更新包的目录均已检查，但本次渲染资源没有合并 `Saved` 更新包。不能把这份快照直接标成某次在线运行的最终参数。

## 接手先读这些文件

| 文件 | 用途 |
| --- | --- |
| [PCResources/README.md](../PCResources/README.md) | 导出范围、格式、验证与限制 |
| [source_manifest.json](../PCResources/source_manifest.json) | 工具版本、文件 SHA256、原始包来源 |
| [Environment/scene_parameters.json](../PCResources/Environment/scene_parameters.json) | 9 张候选地图：129 灯光组件、7 后期 Volume、7 粒子组件、雾／反射数据 |
| [Environment/Dependencies](../PCResources/Environment/Dependencies) | 补取的灯光蓝图／模板、真实导出索引和实例继承证据 |
| [Lighting/postprocess_profiles.json](../PCResources/Lighting/postprocess_profiles.json) | 后期字段值与 override 状态；桌面和移动 Bloom 分开记录 |
| [Lighting/pc_quality_settings.json](../PCResources/Lighting/pc_quality_settings.json) | 41 项本地画质配置、128 项历史设置请求；不是当前有效 CVar dump |
| [Shading](../PCResources/Shading) | 皮肤、发丝、眼球的 PC 母材质／函数／SSS 依赖与可恢复程度 |
| [Metadata/Avatar/Character](../PCResources/Metadata/Avatar/Character) | 角色 VMI 参数和网格的具体材质槽绑定 |
| [Environment/HDR](../PCResources/Environment/HDR) | 已导出 6 个 HDR 全景 |
| [Environment/Particles](../PCResources/Environment/Particles) | 5 个背景粒子包的 emitter／module／cooked distribution 参数 |
| [Tools/PCAssetPipeline](../Tools/PCAssetPipeline/README.md) | 本次实际使用的工具与固定依赖准备、导出和验证入口 |

后期 profile 可用普通 Python 重建和检查：

```bash
python3 Scripts/build_pc_postprocess_profiles.py
python3 Scripts/build_pc_postprocess_profiles.py --check
```

## 场景选择：先标准人物页，再逐套服装

场景名是静态依赖证据；没有在运行中的原游戏确认全部路由。特别保留 Queen 的 Role／Preview、Naimei 两个拼写分支，以及 Bianca 的 `EditorOnly` 路径，不能把多个分支灯光叠加到一个场景。

| 场景候选 | 光照地图，均以 `/Game/Maps/` 为前缀 | 真实天空引用 |
| --- | --- | --- |
| 标准人物页 | `Lobby/Role/Lobby_Scene_Light_Role` | `IBL_Role01_PC` |
| Queen Role | `Lobby_New/Role_Queen/Lobby_Scene_Light_Role_Queen` | `Reflection_Atelier_PC` |
| Queen Preview | `Lobby_New/Role_Queen/Lobby_Scene_Light_Lobby_Queen_Preview` | `IBL_Role01_PC` |
| Lucia | `Lobby_New/Role_AngelLucia/Lobby_Scene_Light_Role_Role_AngelLucia` | `Office_Lounge` |
| Naimei 旧分支 | `Lobby_New/Role_Nai_Mei/Lobby_Scene_Light_Role_Nai_Mei` | `IBL_NaiMei_sky_HD` |
| Naimei 另一分支 | `Lobby_New/Role_Naimei/Lobby_Scene_Light_Role_Naimei` | `MiddayPanorama_EV0_HD` |
| Bianca 候选 | `Lobby_New/Role_NaimeiBianca/EditorOnly/Lobby_Scene_Light_Role_NaimeiBianca` | `IBL_Treasure_HD` |

标准背景另有 `Lobby/Role/Lobby_Scene_General_Background` 和 `…_HD`。背景与光照是分开的来源，不能只导入背景网格便认为人物受光已恢复。Bianca 候选天光实例强度明确为 `0`，应保留这个值，不因存在 HDR 引用就强制点亮。

## 灯光：实例、模板和默认值要分清

读取顺序是：原对象的模板继承链 → 地图实例显式覆盖 → 当次运行时的构造／逻辑修改。此次新增的依赖数据覆盖前两项的可读取部分；原生类未序列化的默认值、构造脚本及在线运行修改仍需单独确认。

本次模板补取为 10 个包、148 个 JSON，闭包两层。`Dependencies/object_index.json` 对 695 个对象建立真实导出序号关联，184 条 Template 引用身份全部核对；`resolved_components.json` 保留 266 个相关对象，包括全部 129 灯光、7 后期区域、18 相机组件和 49 场景组件。176 个对象具有已解析模板跳转，没有无法定位的模板终点；这不表示原生默认值或构造脚本已经执行。每个合并字段都有来源，拓扑引用只使用地图实例自己的序号。

继承验证检查了 8,663 个字段来源、3,810 个继承字段、420 个显式 false 和 3,724 项实例拓扑，均通过。接手机器有 PowerShell 7.6 时，可直接运行 `Tools/PCAssetPipeline/Lighting/Validate-Templates.ps1` 重复这项纯文件检查。

标准场景的 `SpotLight_Main` 是核对样例：地图实例明确记录 `Intensity=14`、`IntensityUnits=ELightUnits::Lumens`、`bAffectsWorld=true`、`InnerConeAngle=2`、`OuterConeAngle=10`、`AttenuationRadius=600 cm`、`SourceRadius=50 cm`、`Temperature=6000`、`bUseTemperature=true`、`ShadowBias=.05`。其模板记录的强度／单位和开关不同，必须被地图覆盖，不能直接搬模板值。实例方向灯的 `bAffectsWorld=false` 也必须保留，不能把所有导出灯都生成。

| 原数据 | 当前工程可承接位置 | 实施要求 |
| --- | --- | --- |
| 位置、旋转、颜色、强度、温度、半径、锥角、光源尺寸、阴影偏移、通道 | `FViewerEnvironmentLight`，`ViewerPawn::CreateStage` | 先按模板解析实际值和有效开关，再写候选；缺省不能填 0 |
| 强度单位 | `IntensityUnits` → `ELightUnits` | 按具名枚举映射，记录单位；导出器附加的 `IntensityNits` 不能替换原 Intensity |
| 相对变换／父组件 | `Location/Rotation` 最终需为舞台根坐标 | 合成完整 parent／actor 链，记录旋转／scale；不能把 RelativeLocation 当作已确认世界位置 |
| 字节 LightColor | `FLinearColor` | 按 sRGB→linear 转换；不要对原已是线性的向量再做一次 gamma 转换；色温另行保留 |
| Cubemap、天光强度／颜色 | `UViewerEnvironment.Cubemap/SkyIntensity/SkyColor` | HDR 方向、亮度归一化、原 `CubemapAverageBrightness`／旋转需核对 |
| `bAffectsWorld/bVisible/原Mobility` | 当前数据模型未完整表达 | 扩展数据模型或明确筛选规则；保留来源证据 |
| RectLight、LightFunction、IES、反射捕获、ShadowVolume、fork 微阴影 | 当前没有完整承接字段／生成路径 | 实现对应组件／材质／shader，或列为未实现；不要静默忽略后称一致 |

HDR 源为 128×128／面的 FloatRGBA cubemap，当前发布的是重投影后的 443×221 Radiance 全景。涉及投影重采样和 RGBE 量化，不能称原 cubemap 无损字节。先验证导入后方向与亮度，再调整人物材料。

## 后期：值存在不代表 override 生效

使用 `postprocess_profiles.json` 中的字段证据，保留以下区别：

- `value_and_explicit_override`：序列化了值且对应 override 明确为 true；仍不证明 Volume 最终混合进当前帧。
- `override_without_serialized_value`：override=true 但值没有序列化，需继续查模板／源默认语义。
- `value_without_explicit_override`：只有值，不能自动开启 override。
- `explicit_disabled_override`：override 明确为 false，不能因为值看起来重要就启用。

标准人物页的关键样例：

| 原设置 | 已读到的事实 | 不能直接推断的事项 |
| --- | --- | --- |
| `DesktopBloomSettings` | 强度 override=true、Intensity=2；阈值 override=true、Threshold=1 | 原 fork Bloom 与 UE5 原生 Bloom 的数值响应相同 |
| `MobileBloomSettings` | 有 Intensity=2.631746，阈值=.232；强度 override 未显式序列化 | 移动 Bloom 强度已经确认生效，或应直接覆盖桌面强度 |
| 曝光 | MinBrightness／MaxBrightness override=true，值均为 0 | 原引擎的 0 与 UE5 EV100=0／Manual EV0 等价 |
| 景深 | `bOverride_DepthOfFieldScale=true`，Scale 值未序列化；另有近／远距离和过渡参数 | 当前查看器的 Fstop=2.8、焦距=相机距离就是原相机景深 |
| Lens flare | 序列化值 3.7，没有启用该 override 的证据 | 应按 3.7 启用眩光 |
| 色彩 | `ColorContrastMidtones=(1.1,1.1,1.1,1)` 且 override=true | 可以忽略原材质、曝光与 tonemapper，只靠提高对比度匹配人物 |

桌面／移动分支应分别建立平台候选。`Settings`、相机后期、Volume 的 Priority／BlendWeight／范围、动态覆盖需要一起核对；不能把所有 Volume 合成一个无界后期组件。`IsUI/UIRenderResolution` 等 fork 字段没有现成的 stock UE 等价实现时应单列。

## 当前代码必须先处理的覆盖点

这些位置已在基线源码核对。接手机器应为候选增加明确的“来源参数模式”，与当前查看器画质模式隔离；具体字段名由实现决定。下面是待完成的实施项，当前仓库没有宣称它们已经改好。

| 位置 | 现有行为 | 来源模式应处理的内容 |
| --- | --- | --- |
| `ViewerPawn.cpp`，`ApplyPreferences` | 强制 Manual 曝光、physical exposure=false | 保留来源候选的曝光选择或明确记录转换公式 |
| 同函数及相机更新路径 | 景深 Scale=1/0、Fstop=2.8、焦距=Distance；移动镜头再次对焦 | 给原相机／Volume 景深留出来源控制权 |
| 同函数 | AOQuality=100；SSR 强度／质量=100、MaxRoughness=.8；桌面 GI=Lumen | 来源选项不能被这些常量覆盖；Lumen 不是原私有 PRT／GI 的恢复 |
| `ViewerPreferences.cpp`，`ApplyEngineSettings` | BloomQuality=4、DOFQuality=2、Sharpen=.35、TonemapperQuality=5、SSR HalfRes=0 等 | 记录最终 CVar 和优先级，分离质量设置与来源外观参数 |
| `ViewerPawn::CreateStage` | 灯全为 Movable；天光不投影；方向灯强制 16m／3级 CSM | 保留来源 mobility／阴影策略或明确记下兼容改动 |
| 同函数 | 单个后期组件始终 Unbound | 实现所需 Volume 范围、优先级和混合 |
| 相机拟合与 UI 布局路径 | 会改变 FOV／旋转和景深；`CameraHorizontalFOV` 未形成严格来源镜头路径 | 视觉对照时固定来源镜头和构图，不用自动拟合后的镜头冒充原视角 |
| 雾创建路径 | Albedo、scattering、distance、FSSS 多项硬编码；移动端跳过 | 根据来源数据扩展参数，并分别验收桌面／移动路径 |
| `import_environment.py` | 旧导入器写 Manual EV0、Bloom=.15，关闭 DOF／flare | 新 PC 桥接导入不要沿用这些兼容常量 |
| `calibrate_viewer_lighting.py` | 把标准三 Spot 改成 80/25/80 lm，并统一 AO／Bloom／色彩 | PC 来源候选导入后不要自动重跑旧校准 |
| `refine_character_surfaces.py` | 肤 Specular=.35、眼 roughness=.045 等查看器校准 | 来源材料候选单独处理，保留旧材料作 A/B |

此外人物当前接收灯光通道 0+1，场景默认通道 0。先确定源通道行为，再引入人物专用补光；不能把额外补光当成原全局光照。

## 皮肤、眼球和毛发决定人物的受光表现

不能只还原灯光，继续用简化材质就承诺原人物效果。VMI 的 Parent、纹理参数、标量、静态开关、SSS profile、眼部反射控制需要一起移植。

新增 `Shading` 依赖中可以读到部分母材质表达式与函数证据。**部分表达式对象存在，并不代表完整、可重新连接的 UE5 图已经取得。** 应检查表达式数量、根属性连接、函数输入输出和缺失对象，逐个记录。原 shading-model 名称与 UE5 同名枚举也不能证明原 BRDF、预积分 LUT、双高光、透光、散射和眼球反射算法完全相同。

SSS profile 数值保留原值和单位未知状态，不把旧参数直接填成 UE5 的 MeanFreePath／WorldUnitScale。没有明确纹理／函数引用证明的 LUT 不自行命名或补造。

支撑纹理也不能只按文件名分类：已补取的 `T_Hair_Detail_Normal` 实际为 `TC_Default/PF_DXT1`、`IsNormalMap=false`、`sRGB=false`，原函数采样类型为 `LinearColor`；不要因为名字含 Normal 就改成法线压缩。另有 `Hair_Noise`（128²、sRGB=true）与眼球中平面位移（256²、sRGB=false），具体设置见各 JSON。

现有 `upgrade_character_shading.py` 是 stock UE 候选适配工具，曾有移动端银发、脸部斑点等试验记录；不是 PC shader 一键恢复器。若使用，先读 [CHARACTER_SHADING_REUSE.md](CHARACTER_SHADING_REUSE.md) 和实际脚本／allowlist，仅 prepare 独立候选并验证，不直接全角色 apply。

## 粒子、反射动画与角色动态参数

已提取 5 个背景粒子系统，125 个 JSON，包含 11 个 emitter、87 个 module 和 102 组 cooked distribution 参数。原 `Light_Top` 没有可读 emitter 数据时已经记录缺失，不补造。

这些 JSON 不会被 C++ 编译器自动重建为 Cascade／Niagara 资产。后续应恢复对应发射器、module、采样表、材质／纹理依赖、局部／世界空间、挂点、组件变换、启用条件和时间。cooked distribution 采样不能自动还原成原可编辑曲线。

角色眼部高光、HDR 角度、皮肤粗糙度等还可能由动画／Sequence／蓝图动态修改。已保存的静态材质值不等于待机全过程。参见 [LIGHTING_TRACK_EVIDENCE.md](LIGHTING_TRACK_EVIDENCE.md) 与 [FACIAL_ANIMATION_EVIDENCE.md](FACIAL_ANIMATION_EVIDENCE.md)；先确定当前 RoleIdle 范围，再决定是否恢复动态轨道。现有工程取消了过场，不能为还原待机光照而误接入原登场演出。

## 另一台机器的实施顺序

1. **验证输入与基线。** 拉取当前仓库，安装 UE5.8.4 和该平台编译工具；先用已有 Content 编译／运行，保存基线截图和实际画质设置。运行下面的无引擎检查。
2. **建立独立来源候选。** 新增／适配 PC 数据桥接导入器，将模板解析后的参数、HDR 和相机写入 `/Game/Viewer/PCEnvironment/...` 独立资产；保留当前环境／材质用于 A/B。现有 `import_environment.py` 固定需要已删除的 `SourceAssets/Environment` 聚合格式，不能直接运行它来消费这批逐对象 JSON。
3. **完成来源模式。** 按上表处理运行时和导入器覆盖，扩展缺失环境字段／组件。先标准人物页：灯光→HDR／反射→曝光／色调→Bloom／AO→景深；保持同一角色、服装、贴图与动作。
4. **恢复材质。** 先单角色肤／眼／发逐项 A/B，核对透明轮廓、头发方向性高光、阴影、口腔／眼球亮点和肤色。需要私有 shader 方程而未取得时，明确标为近似，保留缺口。
5. **重建特效。** 按粒子报告和依赖还原背景灯线／微粒及必要动态参数轨道；先验证无粒子，再开启粒子比较，避免把 Bloom 与自发光粒子混淆。
6. **验证角色场景路由。** 为 Queen／Lucia／Naimei 等逐个确认场景分支。只定点改 `ViewerOutfit.Environment` 或候选材料引用，不用小清单重建整个 Catalog。
7. **编译与真实渲染验收。** 重新构建、导入／保存编辑资产、等待真实 shader 编译完成，再运行冒烟与灯光旋转采集。最后根据目标做 cook／打包与设备测试。
8. **提交接手结果。** 提交实际 `.uasset`／必要 C++／脚本变更、参数来源报告、明确的近似清单和截图证据；分别报告编译、结构、shader、视觉与设备状态。

无引擎文件检查：

```bash
python3 Scripts/validate_project.py --require-content
python3 Scripts/validate_pc_resources.py
python3 Scripts/build_pc_postprocess_profiles.py --check
```

现有 Mac 构建／运行入口（源码基线支持 Mac／iOS；若接手机器使用 Windows，需要先处理该工程的平台支持，不能把 Mac 脚本当 Windows 构建入口）：

```bash
export UE_ROOT="/absolute/path/to/UE_5.8"
Scripts/build_macos.sh
./OpenViewer.command -ViewerSmokeTest
./OpenViewer.command -ViewerLightingSweep -ViewerSmokeCharacter=0 -ViewerSmokeOutfit=0
```

Catalog 索引从实际数据读取，不固定假定角色顺序。报告位于 `Saved/ViewerSmokeTest/LatestReport.json` 及相应 `Run_...` 目录。运行这些命令只验证当时实际绑定的资产；尚未切换到候选时，它们测到的仍是基线。`-nullrhi` 可用于数据审计／保存，不用于画面和 shader 成功证明。

## 验收记录必须分开

| 项目 | 最低证据 |
| --- | --- |
| 数据 | 来源快照、模板链、每个转换值和未知字段；没有把缺省当 0 |
| 编译／导入 | 目标平台构建、编辑资产保存、无丢失引用；备份可回退 |
| Shader | 实际目标 RHI 编译完成，没有用未实现分支或错误回退冒充成功 |
| 画面 | 同角色／服装／RoleIdle 时间、镜头／FOV、分辨率、曝光和平台路径的截图；人物前／侧／后与近景 |
| 运动稳定性 | 静止→±45°旋转→停止；检查闪烁、反射跳变、透明排序、发片亮度和阴影 |
| 平台 | Mac 桌面 SM6、移动预览、iOS 实机分别记录，不相互替代；SSR 开关与实际 CVar 一并记录 |
| 原游戏对照 | 有授权取得的同场景画面，记录原版本和设置；缺少对照时不写“1:1通过” |

当前已完成的是资源提取、数据／文件核验及实施交接；候选环境桥接、来源模式、shader／粒子重建和真实画面验收仍在接手机器执行。此状态必须与文档和提交说明保持一致。
