# UE 5.8 渲染能力核对

核对：2026-11-08；本机 UE **5.8.4**、Apple **M5**、Metal **4**、macOS **27.2**。此表区分官方能力、平台声明和项目实际运行证据；不以开关值代替 GPU pass 证据。

UE 5.8 将 MegaLights 提升至 Production Ready，并增加透射、froxel 透明照明和高质量首层透明照明；FSSS 则仍为 Experimental。5.8 也新增 Mac Metal Ray Shaders。不能沿用较早版本中 Mac 不支持这些 RT 路径的结论。[Epic 5.8 Release Notes](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-5-8-release-notes)

| 功能 | 原生 Mac 桌面 SM6 / M5 | 本项目当前 iOS ES3.1 移动前向 | 项目使用判断 |
|---|---|---|---|
| MegaLights 局部灯直接光与软阴影 | **本机已执行 GPU pass**：原生 Metal SM6、RT 开启，捕获到 MegaLights 和 inline HardwareRayTraceLightSamples。 | **不支持**当前路径；官方明确排除 Mobile 和 Forward Renderer。 | 用现有 Point/Spot 灯做开关 A/B；灯少时未必比原路径更快。MegaLights 提升直接光，不等于 GI。 |
| MegaLights 高质量首层透明照明 | 本机捕获到 `MegaLightsFrontLayerTranslucency`。5.8 第一层逐像素，后层使用体积近似。 | 不因材质写着 Surface Per Pixel Lighting 就获得桌面功能。 | 对现有透明发片值得评估，须检查交叠发片、噪声和拖影；不能替代颜色、切线或透明覆盖适配。 |
| Lumen HWRT | Mac SM6 探针已捕获 HWRT 路径；本次场景 GI=Lumen，反射方法仍为用户选择的 SSR。 | 当前 ES3.1 路径不能据 Mac 配置宣称启用。 | 分开评价间接光和反射，再比较 MegaLights 共享 RT 场景的收益。 |
| **FSSS：Fog Screen Space Scattering** | **可选原生组件入口已实现，待统一编译和 GPU 验证**。不依赖硬件 RT；需要 separate fog composition 与所需纹理/UAV 路径。 | 本项目明确不创建该组件；未据组件存在宣称移动支持。 | 用于浓雾、烟尘多散射；默认关闭，按环境资产或命令行启用，跟随后处理可见性。 |
| 皮肤 Subsurface Profile / Preintegrated Skin | 桌面 Profile 已有独立候选。 | ES3.1 使用独立 Preintegrated Skin 分支。 | **与 FSSS 分开验证**；FSSS 不修复皮肤散射、嘴部穿模或材质贴图问题。 |

MegaLights 的平台边界、首层透明行为及性能要求依据 [Epic MegaLights 文档（5.8）](https://dev.epicgames.com/documentation/en-us/unreal-engine/megalights-in-unreal-engine)。FSSS 的雾组件入口和实验状态见 [Epic UExponentialHeightFogComponent API（5.8）](https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Runtime/Engine/UExponentialHeightFogComponent)。本机 Mac 可试验的判断来自下述本机声明，并非本次已经运行通过。

## Mac SM6 与 MegaLights 门槛

- `Engine/Config/BaseEngine.ini:3434` 的 Mac TargetedRHIs 同时包含 `SF_METAL_SM5`、`SF_METAL_SM6`；这说明两种目标可用，不证明某次场景实际使用哪一种。原生桌面试验明确传 `-sm6`，移除 `-FeatureLevelES31` 和 `-PreviewPlatform=IOS`。官方命令表定义了 `-sm6` 强制 SM6、`-sm5` 强制 SM5。[Epic 命令行参考（5.8）](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-command-line-arguments-reference?lang=en-US)
- `Engine/Config/Mac/DataDrivenPlatformInfo.ini:68`：`METAL_SM6` 的 RT、RT shaders、inline RT、Wave Ops、GPUScene 均为支持；同文件 SM5 的 RT/inline RT 为 false。`Config/Mac/MacEngine.ini` 已请求 RT、Skin Cache、Lumen HWRT，但没有单独开启 MegaLights。
- 项目编译入口是 `r.MegaLights.EnableForProject`；透明首层入口是 `r.MegaLights.FrontLayerTranslucency.EnableForProject`。运行时还看 `r.MegaLights.Allow`、Post Process 覆盖、灯的 `Allow MegaLights`、质量档与实际灯类型。本机 `RendererSettings.h:651`、`:656` 和 `Scene.h:1884` 可核对这些字段及 SM6/RT 要求。
- 平台能力声明不等于实际 RHI。`DataDrivenShaderPlatformInfo.h:1204` 明确要求运行时另查 `GRHISupportsRayTracing`、`IsRayTracingEnabled()`；inline 路径还查 `GRHISupportsInlineRayTracing`。MegaLights 另外区分 `ShouldCompileShaders`、硬件/软件追踪、inline RT 与逐灯模式，见 `Renderer/Private/MegaLights/MegaLights.h:54`。本机安装包未包含这些函数的 `.cpp` 实现，因此本表不假装已枚举全部内部条件。

此前 ES3.1 预览日志即使出现 `ShaderAutogen/METAL_SM6`，最终窗口和场景仍是 `SF_METAL_ES3_1`，并报告当前 RHI 不支持 RT。原生 Mac 试验必须同时核实 `maximum_rhi_shader_platform`、`scene_shader_platform`、`scene_feature_level_name` 和运行时 RT 标志；再在 GPU 捕获中看到 MegaLights sampling / tracing / denoising，而不是只检查 CVar 为 1。

## FSSS 的实际入口与限制

本机 `ExponentialHeightFogComponent.h:195` 将它定义为雾的屏幕空间多散射近似，入口为 `bEnableFSSS`；可调 scene-color scattering scale/power、spread、blur。`FogSeparateComposition.h:65` 明确说明，只有雾使用独立纹理合成时，场景的 FSSS 请求才会生效。`FogSeparateComposition.usf:246` 还标注 typed UAV load 要求；Mac SM5/SM6 都声明四分量 UAV 读写支持，但这仍不代替真实 fog pass 验证。

官方 5.8 说明 FSSS 可作用于 Volumetric Fog 与 Local Fog Volumes，暂不覆盖 Volumetric Clouds 或 Heterogeneous Volumes。它会模糊浓雾后的场景，过大 spread 可能产生深度边缘光晕。对需要清晰看脸、材质和发丝的查看器，应先作为可切换的环境效果，以相同镜头、曝光和雾密度做开关比较；不要将模糊误判为皮肤质量提升。[Epic 5.8 Release Notes](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-5-8-release-notes)、[Epic 雾组件参数](https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Runtime/Engine/UExponentialHeightFogComponent)

本轮优先顺序：**原生 Mac SM6 + 实际 RT 能力 → Lumen / MegaLights 局部灯 A/B → 发片首层透明照明 → 有实际雾场景时再单测 FSSS**。iOS ES3.1 保持独立验证；5.8 新增实验 iOS SM6 不代表 MegaLights 的官方移动支持已经成立。

## 已有项目实测与可选雾入口

`Saved/ViewerSmokeTest/Run_20261107T182448_62423/Report.json` 记录真实 scene 与 maximum RHI 均为 `METAL_SM6`、`unreal_rhi_supports_ray_tracing=true`、`unreal_ray_tracing_enabled=true`、`r.ForwardShading=0`。`Saved/FullRendering/mac-sm6-megalights-probe.log` 捕获 MegaLights、`HardwareRayTraceLightSamples Inline`、Lumen HWRT、`MegaLightsFrontLayerTranslucency` 和 `SSS::PassOne_Burley`。这些是实际执行路径证据；单帧计时不能作为稳定 benchmark，也不证明所有角色外观或真机效果已通过。

环境可选字段为 `bEnableFogScattering=false`、`FogDensity=.003`、`FogHeightFalloff=.2`、`FogStartDistance=100 cm`、`FogColor`。原生桌面 SM5+ 可通过环境布尔值或 `-ViewerFogScattering` 创建 `UExponentialHeightFogComponent`，启用 Volumetric Fog 和 FSSS；iOS 编译分支及 ES3.1 场景不创建。初始 FSSS scene-color scale `.15`、power `1`、spread `.15`、blur `.35`，只用于保守起始校准。组件随舞台销毁，后处理关闭时隐藏。此入口仅改运行时代码和数据资产类定义，未重存环境 Content；实际雾 pass 和开关画面需要编译后另验。
