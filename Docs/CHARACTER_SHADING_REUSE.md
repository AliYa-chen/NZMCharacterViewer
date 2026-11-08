# 皮肤与毛发实现复用核对

核对日期：2026-11-08。目标是本项目 UE 5.8.4、Mac/Metal 与 iOS 移动前向渲染；优先复用现成 BRDF、保留 NZM 贴图和角色外观。此次只读取 GitHub 的 README、根目录和许可证，没有导入第三方代码、资产或插件。仓库提交、更新时间及根目录证据记录在 `Saved/LightingDebug/github_material_candidates.json`。

## GitHub 候选

| 实现 | 可复用内容 | 已核实限制 | 对本项目的结论 |
|---|---|---|---|
| [tigershan1130/UE4-Mobile-Skin-Shading](https://github.com/tigershan1130/UE4-Mobile-Skin-Shading) | 移动皮肤 LUT、球面高斯近似的示例及引擎改动链接。 | README 明确要求 OpenGL ES 3.1 和自定义 UE 4.26.2；根目录及 GitHub 元数据未找到许可证声明。 | 适合研究比较，不能作为 UE 5.8/Metal 的即插即用依赖；当前不复制其代码或资产。 |
| [tigershan1130/UE4-Kajiya-Kay-Mobile-Hair-](https://github.com/tigershan1130/UE4-Kajiya-Kay-Mobile-Hair-) | 发片 Kajiya–Kay 方向性高光及环境照明的管线参考。 | README 明确依赖自定义渲染管线、OpenGL ES 3.1 和 UE 4.26.2；根目录及 GitHub 元数据未找到许可证声明。 | 需要移植引擎 shader，不能直接安装到当前项目；当前不复制其代码或资产。 |
| [iryoku/separable-sss](https://github.com/iryoku/separable-sss) | 成熟的双 pass 屏幕空间 SSS 算法和集成说明。 | 示例使用 DirectX 10，并非 UE/Metal 插件；[许可证](https://github.com/iryoku/separable-sss/blob/master/LICENSE.txt)允许再分发，但有保留声明和二进制归属要求。 | 可作为桌面算法参考；UE 已有 Subsurface Profile，重复移植会增加移动渲染集成工作。 |
| [GPUOpen-Effects/TressFX](https://github.com/GPUOpen-Effects/TressFX) | 完整毛发模拟与渲染库，MIT 许可。 | 当前仓库样例后端是 DX12/Vulkan；README 的 UE 集成为 4.22 引擎补丁，未提供 Metal/iOS 后端；还需专用毛发数据。 | 无法直接复用 NZM 现有发片与当前 Metal 管线；不作为本轮依赖。 |

本次筛选没有找到能直接满足 UE 5.8.4 + Metal/iOS + 现有 NZM 发片输入的成熟替换插件。这个结论仅限已核实候选，不代表 GitHub 上不存在其他实现。已存在的 UE 皮肤与 Hair BRDF 是当前可直接调用的成熟实现；适配工作应集中在模型分支、贴图语义、透明度和发丝方向。

## 当前可复用的 UE 路径

本机 MetaHuman 材质只读对照在 `Saved/LightingDebug/official_character_material_comparison.md`；它证明官方材质有平台分支和更完整输入，不能证明换一个 shading-model 枚举就能恢复原游戏效果。

- 皮肤：复制现有材质图；保留 BaseColor、Normal 和现有表面响应。`FeatureLevelSwitch` 在 ES3.1 选择 UE `Preintegrated Skin`，桌面选择 UE `Subsurface Profile`。移动分支不等价于桌面屏幕空间 SSS。源资产中确有的 profile 参数可用于建立独立 profile，不能据此宣称恢复 NZM 自定义 LUT、双高光和散射管线。
- 发片：复制现有材质图；保留 NZM 已适配的颜色、Opacity、双面与 Translucent 混合。调用 UE `Hair` BRDF，按现有 UV 的 V 方向建立每像素世界空间发丝轴，连接 Hair 模型内部 `Normal`/界面 `Tangent` 输入。不能重新使用此前固定 `(0,1,0)` 切线、强制 Masked 与固定深蓝色的失败试验。
- 发丝轴可用标准 UV/位置偏导节点求解，属于几何方向适配；不需要自行编写 Kajiya–Kay、Marschner 或 SSS 的光照公式。退化 UV 必须有有限值保护，并在验证报告中记录。

## 明确的能力边界与验证门槛

本机 `Engine/Shaders/Private/MobileLightingCommon.ush:560` 先将移动 SSR 合成到 `SpecularIBL`，同文件第 611 行的 Hair 分支随后明确跳过 IBL。因而移动 Hair 本身不能仅靠开启全局 SSR 获得该镜面反射项；材质方向适配也不会补齐它。若要求发丝同样具备完整 SSR/IBL，需要另做当前引擎移动 Hair 路径的兼容实现及 GPU 验证。

此前 Angela 原生 Hair/SSS 对照已在 iOS 出现银发、锯齿和面部斑点，拒绝记录为 `Saved/AngelaMaterialTrialRejected.json`。新的适配先只创建独立候选资产，默认不改变 Catalog。必须完成真实 shader 编译、Mac 移动前向截图与 iOS 真机 A/B；基色、轮廓、透明边缘、面部斑点和光照移动响应通过后，才能切换正式引用。静态 Python 校验或 NullRHI 资产读取均不能作为画质通过证据。

## 独立候选适配脚本

`Scripts/upgrade_character_shading.py` 目前只接受 `-CharacterShadingFilter=angela`，包含已审计的头、身体皮肤及首层发片三项。未审计其他角色前不泛化处理。

| 参数 | 行为 |
|---|---|
| `-CharacterShadingMode=audit` | 默认模式；读取现有资产与图连接，不保存 Content。报告只表示图结构符合适配要求。 |
| `-CharacterShadingMode=prepare` | 创建 `/Game/Viewer/CharacterShading/angela` 独立候选；不改 Catalog。 |
| `-CharacterShadingMode=apply` | 先确认候选可准备，再备份 Catalog 原始字节和引用，最后切换所选材质。 |
| `-CharacterShadingMode=original` | 根据原始 manifest 恢复 Angela 基线材质引用；不会恢复此前其他任务的 Catalog 字节。 |
| `-CharacterShadingParts=skin/hair/both` | 选择候选。`apply skin` 会恢复基线毛发，`apply hair` 会恢复基线皮肤，便于独立 A/B；`original` 恢复两项。 |

脚本使用纯 UE 材质节点，不新增 shader mapping。Python 未暴露的 `MP_ShadingModel` 通过 `MakeMaterialAttributes.ShadingModel` 输入连接，既不写隐藏枚举索引也不改引擎。移动发丝方向节点使用材质表达式全精度，保留镜像 UV 的符号；UV 或位置偏导退化时使用网格切线基的 V 轴，避免除零。

起始数值明确属于查看器校准：移动皮肤 SubsurfaceColor `(0.08,0.02,0.015)`、Opacity `0.85`；桌面 Burley profile MeanFreePathDistance `0.12 cm = 1.2 mm`、WorldUnitScale `1`。这些数值需要 A/B 调整，不能标记为原游戏参数恢复。每次运行在 `Saved/CharacterShading/report_<mode>_<parts>.json` 写出保存/不支持/失败状态，以及原材质和 Catalog 的前后哈希；shader 编译与实际画质验证始终另报。

### Hair 第二次校准与白发诊断

原始候选在 Mac 移动前向截图 `Run_20261107T180144_57790/Sweep_000.png` 中仍出现明显银白发束，不能用于正式装机。静态 shader 核对发现：

- `MobileLightingCommon.ush:313` 使用 `InitHairTransmittanceData()`；`HairShadingCommon.ush:39` 将 `bUseBacklit` 设为 `false`。因此 `HairBsdf.ush:285` 的材质 Backlit 输入在这个移动方向光路径被忽略，单独将它从 `1` 改成 `0.1` 不能证明透光已减少。
- `HairBsdf.ush:335` 的 R 反射项直接乘 `GBuffer.Specular * 2`，不乘 BaseColor。保留原材质的 `Specular=0.5` 并不保留原先 Default Lit 的白色高光能量；换 BRDF 后必须单独校准。
- `MobileBasePassPixelShader.usf:1031` 还调用 `EvaluateEnvHair()`，后者在 `HairStrandsEnvironmentLightingCommon.ush:75` 固定 `Backlit=0` 并评估原生 Hair 来计算间接响应。该项也受原生 Specular 和 Roughness 影响。

校准 v2 保持已有候选的 BaseColor、Opacity、发丝方向与混合模式，设置可复现的 `Specular=0.2`、`Roughness=0.55`、`Scatter=0.25`、`Backlit=0.1`。四项可用 `-CharacterHairSpecular`、`-CharacterHairRoughness`、`-CharacterHairScatter`、`-CharacterHairBacklit` 覆盖。只有脚本所有权、原始源材质路径与 SHA256 均匹配的候选可以更新；皮肤候选和原材质不因此重建。

`Specular=0` 可用于诊断 R 高光是否为主因；如果零高光仍发白，需要继续定位原生 Hair 的 TT/TRT/多散射与输入语义，不能靠改变角色发色或加自发光掩盖。参数更新只构成候选，必须另看真实 Mac/iOS 结果。

### Hair 输入语义校准 v3

移动预览 `Run_20261107T181631_60326` 的 47 张截图完成了 `Specular=0` 诊断，发束仍明显银蓝；R 白高光并非充分解释。只读导出的真实候选图确认 BaseColor 没有丢线：原 Lerp 的 Alpha 为 `1`，输出原深蓝常量 `(0.003223,0.003937,0.010417)`；Opacity、世界空间 UV-V 轴连接也完整。证据保存在 `Saved/FullRendering/HairSpecularZeroDiagnostic`。

原生 `HairBsdf.ush` 的 KK 多散射近似使用 `sqrt(abs(BaseColor))`，TT/TRT 还使用不同的 `pow`。因此，直接沿用 Default Lit 的线性反射输入会改变亮度含义。v3 在原 BaseColor 图后增加唯一的脚本所有 Power 节点，`Viewer_HairColorPower=2` 是用于抵消 KK 开方抬升的起始校准；它不等价于匹配所有 TT/TRT 光程，也不是原游戏 BRDF 恢复。原色图和颜色常量均保留，未引入新的固定发色或自发光。

`-CharacterHairColorPower=1` 可禁用该转换，参数范围 `0.25..4`。重复运行始终将 Power.Base 接回原材质根节点的克隆，只更新同一 Power 和指数参数，避免平方层层叠加。保存前验证唯一节点、根属性连接与指数输入；报告和 metadata 记录校准版本 `3`、原始节点路径、Power 节点路径和实际指数。其余参数恢复 `Specular=.2`、`Roughness=.55`、`Scatter=.25`、`Backlit=.1`。这个色彩语义转换仍待实际移动预览与真机确认，不能单凭保存或 smoke pass 认定外观通过。

这条复用途径不接入 GI。Enlighten 的 SDK、平台授权和集成仍在 `Docs/ENLIGHTEN_INTEGRATION.md` 中单独跟踪；皮肤或毛发着色变化不构成 GI 已接入。
