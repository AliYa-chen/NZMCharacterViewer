# Enlighten 实际接入条件

核对日期：2026-11-08。当前结论：**本机没有可用于编译／预计算的 Enlighten SDK、UE 集成包或许可文件，工程尚未接入 Enlighten。** 已有补光、HDR、Lumen、SSR 和从游戏包读取的 shader 均不能作为 Enlighten 接入成功的证据。本次只调查和准备文件检测，不修改引擎、运行时、材质或渲染配置。

## 官方版本与目标平台

官方于 **2026-08-28 发布 UE5.8 适配**；4.04 安装文档明确基于 **UE5.7.0 至 UE5.8.0**。本工程使用的 **UE5.8.4** 属于 hotfix，需要匹配的供应商包或将基版 Enlighten 修改与 hotfix 源码做三方合并并验证。不能因旧主页某语言版本仍写 5.7 就认定无 5.8 支持，也不能把 5.8.0 支持直接等同已验证 5.8.4。[UE5.8 官方公告](https://www.siliconstudio.co.jp/middleware/enlighten/en/blog/2026/20260828/)、[4.04 安装与构建](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492629/Install+and+build)。

当前官方平台表包含 **Windows、Android、iOS** 等，未列 macOS。**iOS 作为运行目标受到支持；macOS 作为本项目独立查看器／Editor 目标的支持尚未确认。** 官方另提供在 Mac 上重建 iOS 库的脚本，这不表示 macOS runtime 获得支持。[官方平台表](https://www.siliconstudio.co.jp/middleware/enlighten/jp/)、[重建 Enlighten 库](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492623/Rebuild+Enlighten+from+source)。

预计算和可视工具要求 **Windows 10 x64**；预计算库只提供 64 位 Windows 版本。可行流程是 Windows 工作站生成场景数据，再在受支持的 iOS 运行目标采样。当前 Apple Silicon Mac 不能仅靠已有 UE/Xcode 完成官方 Windows 预计算流程。[SDK 安装要求](https://enlighten.atlassian.net/wiki/spaces/SDK404/pages/3696328898/Install+Enlighten)、[SDK 库与目录](https://enlighten.atlassian.net/wiki/spaces/SDK404/pages/3696330248/Libraries)。

## 获取入口与缺少的文件

[Silicon Studio 官方评估／购买／咨询入口](https://go.siliconstudio.co.jp/contact/products-service/enlighten/)提供申请渠道；FAQ 要求通过联系获取评估版，没有公开匿名 SDK 下载地址。表单收集公司、工作邮箱、开发平台和引擎等信息。本次没有填写／提交表单或发送邮件；公开页面未给出本项目的报价、许可范围或再分发条款，不能自行假定评估许可允许发布。[官方 FAQ](https://www.siliconstudio.co.jp/business/technology/product/enlighten/faq/)。

| 需要取得／确认 | 精确可确认的名称与边界 | 本机状态 |
|---|---|---|
| UE 集成 Base 包 | 官方格式 `EnlightenUE5-[Version]-[Unreal Engine Version]-Base.zip`；需要 UE5.8.0 基版或供应商确认适用于 5.8.4 的包，实际 release／build 号以交付包为准 | 缺少 |
| 引擎修改与 SDK 树 | Base 含修改过的引擎文件、库和工具；库源码位于 `Engine/Source/ThirdParty/Enlighten` | 缺少 |
| 平台库／头文件 | 实际交付的 iOS arm64 库、API 头文件及匹配的构建规则；独立 SDK 的公开布局为 `Src/EnlightenAPI`，含 `Include`／`LibSrc`／`Libs` | 缺少；不臆造 iOS `.a` 文件名 |
| 预计算许可 | 供应商签发的有效限期 license，UE 版放在 `Engine/Binaries/ThirdParty/Enlighten`；官方未公开固定文件名 | 缺少，未读取任何许可内容 |
| 构建脚本 | `BuildAndInstallEnlighten.bat`、`BuildAndInstallEnlighten.sh`、生成的 `EnlightenBuildConfig.xml` | 缺少 |
| 预计算工具／主机 | 官方工具包括 `GeoPrecomp2`、`GeoConvert` 和构建监视器；需要有 D3D 上下文的 Windows 交互会话 | 未配置可用 Windows 主机 |
| 示例工程 | `EnlightenSamples.uproject`，适合先验证原生集成；不属于运行本查看器的替代品 | 未取得 |
| macOS 支持 | macOS Editor、macOS 游戏及当前 Apple Silicon／Xcode 组合需供应商明确确认 | 未确认 |

上表包名／安装路径来自[UE 安装文档](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492629/Install+and+build)、[库重建文档](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492623/Rebuild+Enlighten+from+source)、[SDK Libraries](https://enlighten.atlassian.net/wiki/spaces/SDK404/pages/3696330248/Libraries)。独立 SDK 与 UE 集成 Base 是两种交付方式；取得独立 SDK 不自动提供当前 UE 的全部集成代码。

## 本机只读核对

搜索范围为 `/Users/aliya/UE`、`/Users/aliya/UnrealEngine`、`/Users/aliya/Downloads`、`/Users/aliya/NZMCharacterViewer`，按文件名遍历约 42 万文件，未找到 Enlighten 命名文件；对 `.uplugin`、`.Build.cs`、`.Target.cs`、`.uproject` 内容搜索也没有 SDK 引用。Downloads 顶层 13 个可读 ZIP／TAR 的内部清单未出现 Enlighten／GeoPrecomp／GeoRadiosity 路径。此结论限于这些目录，不声称扫描了整块磁盘、其他账号或远程存储。

本机 `Engine/Build/Build.version` 为 5.8.4，Changelist 58902052，分支 `++UE5+Release-5.8`；有 `Engine/Build/InstalledBuild.txt`。`GenerateProjectFiles.sh` 与 `Renderer/Private/DeferredShadingRenderer.cpp` 缺失，Renderer 私有目录有头文件而无 `.cpp`，是当前安装构建的一部分。它足以编译本项目模块，**不足以据官方流程直接合并并重编译 Enlighten 的引擎修改**。

`NZMCharacterViewer.uproject` 没有 Enlighten 插件，模块 Build.cs 没有 SDK 库／头文件依赖，以下两个官方目录不存在：

- `Engine/Source/ThirdParty/Enlighten`
- `Engine/Binaries/ThirdParty/Enlighten`

从暖暖 APK 提取的原生名称和 Android Vulkan ES3.1 shader 只能用于研究其实现线索；其中没有可供当前工程调用的 C++ SDK 头文件、iOS Metal 库、场景预计算工具或本项目许可。将这些编译结果拷入工程不会构成 Enlighten 集成。

## 接入后必须完成的实际工程工作

官方 UE 交付修改的是引擎源码，需要以完整源码树重建 Editor／相关工具及游戏。预编译 Enlighten 库本身通常可直接使用；只有修改 SDK 或需要换平台 SDK 时才需要重建其库。这与“只给项目加一条 Build.cs 依赖”不同。[安装与构建](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492629/Install+and+build)、[库重建](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492623/Rebuild+Enlighten+from+source)。

当前查看器在 `ViewerPawn.cpp` 运行时创建全部场景 Mesh，并设置为 Movable；地图中没有可直接据此推断存在的 Enlighten 预计算数据。取得 SDK 后仍需将四种环境整理为能在 Editor 中参与预计算的稳定场景／子关卡，保持原坐标；角色骨骼网格使用 Probe 接收间接光。不能只在运行时改一个 mobility flag 就假定该物体已参与离线求解。官方说明非静态／变形对象默认不参与 radiosity，却可接收 probe 光照。[Actor 与 GI 的关系](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492671/How+actors+interact+with+Enlighten)、[Probe Lighting](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492595/Probe+lighting)。

供应商集成引擎中，项目需启用 **Allow Enlighten**，并将 **Dynamic Global Illumination Method 设为 None**。Enlighten GI 使用其场景数据与更新逻辑，不能与本项目现有非移动路径自动开启 Lumen 的逻辑无条件叠加。此处记录正式接入时的改动要求，本次未修改配置。[项目设置](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492571/Use+Enlighten+in+your+project)。

预计算入口是供应商引擎中的 **Build > Enlighten Precompute**，也提供 `EnlightenPrecompute` commandlet；必须实际产生和保存场景数据，再验证加载／运行时更新和人物采样。若预计算没有完成，增加组件名称或开关不能使 GI 生效。[预计算流程](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492575/Precompute+your+level)。

4.04 的已知条件包括：移动前向的 Enlighten 反射需要 `r.Mobile.Forward.EnableClusteredReflections=1`；UE5.8 iOS Metal 的 Instanced Static Mesh 崩溃有 `r.InstancedStaticMeshes.UseInstancedStaticMeshVertexFactory=1` workaround。文档也单列了 Groom、移动 Rect Light 和 RVT 限制。这里不盲目写入这些配置，只在真正集成版本上按场景检查。[4.04 Release Notes](https://documentation.enlighten.siliconstudio.co.jp/wiki/spaces/UE5404/pages/3696492585/Enlighten+UE5+4.04+Release+Notes)。

## 文件就绪检测

`Scripts/check_enlighten_readiness.py` 只清点实际文件并输出阻塞原因；不会创建 provider、加载 SDK、执行许可验证或更改配置。无参数运行检查当前 UE；取得供应商文件后可传目录和真实许可路径：

```bash
python3 Scripts/check_enlighten_readiness.py \
  --engine-root /absolute/path/to/source-engine \
  --sdk-root /absolute/path/to/extracted-vendor-package \
  --license-file /absolute/path/to/vendor-issued-license
```

默认报告为 `Saved/FullRendering/EnlightenReadiness.json`。当前结果为 `blocked_missing_inputs`，包括 SDK／头文件／运行库／预计算工具／license 缺少、引擎源码不完整、hotfix 合并未验证、需要 Windows 预计算主机。即使将来文件齐全，结果最多为“文件存在、仍需供应商／构建／渲染验证”，不会仅凭文件名报告 GI 工作。

## 完成验收的判据

必须在集成引擎、合法可用的 SDK、已生成场景数据上，证实实时 GI 更新和人物 Probe 采样；再对全部 9 套服装、旋转／缩放、移动与桌面路径逐项渲染验证。**SSR 按用户要求开启验收**，记录 preference、`r.SSR.Quality`、AA 和移动反射 CVars，并检查实际反射、闪烁、透明轮廓与稳定性。SSR 负责屏幕内反射，Enlighten 负责间接光；两者不能互相冒充。皮肤散射和毛发 BRDF 另行验证，Enlighten 不自动补全这些材质。
