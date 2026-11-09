# NZMCharacterViewer

基于 Unreal Engine 5.8.4 的非官方离线人物查看器，面向 macOS 和 iPhone／iPad。当前工程包含 5 名角色、9 套服装、40 个运行部件及待机动画。

支持人物／服装切换、触摸与鼠标旋转缩放、视角复位、待机动画重播、隐藏界面、部件开关，以及保存画质设置。标准展台和部分服装场景已导入；Bianca 当前使用标准展台。角色过场演出已取消，切换角色后直接进入待机。

## 克隆、编译与打开

需要 UE **5.8.4**、Xcode 和 Python 3。请自行安装 Unreal Engine；本仓库不包含引擎。

```bash
git clone https://github.com/AliYa-chen/NZMCharacterViewer.git
cd NZMCharacterViewer
export UE_ROOT="$HOME/UE/UE_5.8"  # 改为实际安装目录
python3 Scripts/validate_project.py --require-content
Scripts/build_macos.sh
./OpenEditor.command
```

编辑器打开 `/Game/Viewer/Maps/L_Viewer` 后点击 **Play**。也可运行 `./OpenViewer.command`，使用编辑器可执行文件的 `-game` 模式打开独立窗口；它不是已打包的独立应用。

**普通克隆、编译和运行不需要 `SourceAssets`，也不需要执行导入脚本。** `Content` 已包含完整的可编辑 UE 模型、骨架、动画、材质、地图、界面资源和中文字体。`.uasset`／`.umap` 是工程资产，会随仓库提交；它们不是被忽略的 cooked 或打包产物。

`UE_ROOT` 未设置时，脚本会尝试 `$HOME/UE/UE_5.8` 和 `/Users/Shared/Epic Games/UE_5.8[.4]`。编译产物、缓存、日志及本机签名配置由 `.gitignore` 排除。

## 操作

| 功能 | iPhone／iPad | Mac |
| --- | --- | --- |
| 旋转视角 | 在模型区域单指拖动 | 鼠标左键拖动 |
| 缩放 | 双指开合 | 滚轮 |
| 人物／服装 | 点选头像或服装 | 点击头像或服装 |
| 复位、重播、部件、设置 | 使用界面按钮 | 使用界面按钮 |

运行时按服装加载软引用资产，准备完成后切换显示；拖动时相机围绕固定的人物与场景旋转，使局部灯和 HDR 保持同一世界坐标。人物按安全区与界面留下的区域构图，窗口改变后重新拟合；复位、设置和布局变化会清理旧视图的历史帧。设置包含帧率、分辨率、阴影、抗锯齿、后期、环境光遮蔽、屏幕空间反射和动态效果；点击设置面板外可关闭。实际效果受设备和渲染路径限制。

标准场景保留原 HDR 与几何，三盏局部灯经过自然光校准：共同主光与轮廓光使用通道 0，人物补光使用通道 1，角色接收两者、场景仅接收通道 0。自定义服装场景保留原灯光与 HDR；后期使用较轻的接触 AO、Bloom 和暗角，皮肤及眼球分别校准镜面反射。可重复应用与原件恢复见 [自然光校准](Docs/NATURAL_LIGHTING.md)。

Mac 的 Metal 配置启用 Lumen 全局光照和反射；本机 Metal SM6 已运行硬件光追，保留距离场以支持软件回退。普通 iOS 继续使用移动前向渲染、TAA、屏幕空间反射与环境光遮蔽。跨设备判断光追是否生效时，应同时核实原生能力、UE RHI 和运行状态。

## iOS

先验证不签名的 arm64 编译与链接：

```bash
Scripts/build_ios_compile.sh
```

这一步不 cook、不签名、不生成可安装 IPA，也不安装到设备。

打包需配置自己的 Apple 开发者账号、Bundle ID 和描述文件：

```bash
export IOS_TEAM_ID="YOUR_TEAM_ID"
export IOS_BUNDLE_ID="com.example.yourviewer"
Scripts/package_ios.sh
```

若使用现有设备／证书的显式选择流程，将 `Scripts/ios_device.env.example` 复制为 `Scripts/ios_device.env`，填写自己的值，再执行 `Scripts/package_ios_device.sh`。安装使用 `Scripts/install_ios_device.sh /path/to/signed.app`。真实配置、描述文件、证书和输出均不提交。

`Scripts/build_ios_cinematic_compile.sh` 用于独立试验 `CinematicIOS` 的 Metal SM6／光追配置，不改变普通移动端配置。本机该试验因引擎缺少 `Engine/Binaries/ThirdParty/Apple/MetalShaderConverter/IOS/libmetalirconverter.embeddedframework.zip` 编译失败，尚未得到可运行的 iOS Lumen／硬件光追版本。

## 验证状态与限制

- 基线工程在不含 `SourceAssets` 的干净 Git 克隆中通过过 Mac Editor 全量编译和 iOS Development arm64 编译／链接。
- 开发版本已完成多轮 iOS cook、签名、打包、真机安装和运行检查。本机记录位于 `Saved/DevicePreparation`；这些结果对应各次被测版本，不能代替取消过场及后续画质修改后的回归验证。
- 最新 Mac Editor 模块及常规 iOS Development arm64 编译／链接已通过。取消过场后的本机 Metal SM6／硬件 Lumen 已完成全部 9 套服装的加载、动画、动态、共转、设置关闭和截图回归，结果为 pass，无 fail／unknown；600×900 竖屏另经运行与截图复核，窄屏保持源相机位置并扩展纵向视野，避免退入场景遮挡。材质缺失着色器编译任务造成的加载等待已修复；此前的 Metal 断言在本轮未复现，不能据此认定所有崩溃路径都已排除。最新安装状态见下方真机记录。
- 保留了原 `RoleIdle` 骨骼待机曲线。Nanami 头部上没有子骨段的动态配置已移除，身体与头发动态保留；这不代表恢复了原插件可能使用的虚拟末端机制。完整面部混合、注视控制和原自定义 shader 尚未恢复；部分眼球、皮肤、毛发、粒子及布料效果仍有差异。详见 [面部动画核对](Docs/FACIAL_ANIMATION_EVIDENCE.md)。
- iOS 17+ 为当前配置下限；帧率和画质选项不代表已验证的真机性能。
- 2026-11-08 的本地 Metal ES3.1 移动预览在 640×900 窗口完成 9／9 套服装的加载、操作和布局检查。相同场景、SSR 关闭的 TAA／无抗锯齿对照保存了各 47 张图，38 对同角样本未检出当前阈值的大范围亮度变化；稀疏截图不能排除快速闪烁，也不能替代 iOS 复核。4× MSAA 的启动与运行中切换对照均出现 Metal 后处理纹理类型不匹配及黑图，仍未解决；原生样本数支持不代表此渲染组合已通过验证。iOS 朝下转垂直的尺寸恢复代码已通过编译与审查，物理姿态切换尚待真机专项验证。

- 2026-11-08 自然光校准后，Mac Editor 与常规 iOS arm64 编译／链接通过；Metal ES3.1 移动预览九套服装回归通过，独立 TAA／SSR 关闭旋转采集保存 47 张图，两轮最终日志未出现纹理类型 ensure。测试中的设置面板初态与输入隔离已修正。随后完整打包并安装至 iPhoneDuo（系统名 HUAWEI Duo），版本 `0.1.0`、build `58902052.0.24`；设备查询确认版本与运行进程，截图确认已进入角色界面。此安装检查不代表真机闪烁、全部服装或姿态切换已完成回归。

## 工程与资源维护

| 目录 | 内容 |
| --- | --- |
| `Source` | C++ 运行模块和构建目标 |
| `Config` | 游戏、输入、渲染、设备和打包配置 |
| `Content/Characters` | 角色、服装、动画、材质和贴图 |
| `Content/Viewer` | Catalog、地图、展台、场景和界面 |
| `Content/Slate/Fonts` | 中文字体、来源与 OFL 许可 |
| `Scripts` | 构建、检查、可选重新导入与设备流程 |

离线预览、原始转换输入、未使用资产和构建输出已从工程中清理。取消过场后，运行时的 `Presentation`／`EntryAnimation` 引用和跳过按钮已移除；重新保存当前 Catalog 后完成依赖清理，累计 73 个不可达资产（约 167.5 MiB）已备份移至本机忽略目录 `Saved/Cleanup`。最新依赖报告包含 634 个工程包，全部可达，无缺失、未知依赖或未使用资产；原 `RoleIdle` 待机资产与曲线继续保留。

贴图的本机重导入路径已清除，283 张贴图经新进程加载核对，原始 mip 像素哈希、源 ID 和压缩／色彩设置保持一致。资源清理依据运行时入口及 UE AssetRegistry 的硬引用、软引用和管理引用闭包，保留字体和代码动态加载的界面资产。可重复运行只读检查：

```bash
"$UE_ROOT/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor" \
  "$PWD/NZMCharacterViewer.uproject" -run=pythonscript \
  -script="$PWD/Scripts/audit_content.py" -nullrhi -unattended -nosplash
```

报告写入 `Saved/ContentAudit.json`；此检查只读取磁盘资产依赖，不验证画面或设备性能，也不会自动删除资源。新增通过代码动态加载的资源时，需同步维护脚本中的入口。

需要重新导入 FBX／PNG 时，见 [导入说明](Docs/ASSET_IMPORT.md) 和 [清单格式](Scripts/MANIFEST.md)。

PC 版新提取的 194 张 PNG、36 个 LOD0 ActorX 网格、6 个 HDR 以及人物页灯光／后期／粒子参数位于 [PCResources](PCResources/README.md)。其中 93 张高清纹理已有当前材质到 PC 参数的绑定核验和定点导入清单；这些源文件尚未在 UE5.8 中导入，现有 `Content` 继续使用原资产。离线工具及固定版本依赖安装入口见 [PCAssetPipeline](Tools/PCAssetPipeline/README.md)，具体差异与证据见 [PC 端资源分析](Docs/PC_ASSET_ANALYSIS.md)。

后续机器优先恢复人物受光、后期与特效时，按 [PC 人物渲染还原交接](Docs/PC_CHARACTER_RENDERING_HANDOFF.md) 执行。文档汇总补取的灯光模板、后期 override、肤／眼／发材质依赖和当前代码中的覆盖点，明确哪些仍需实现、编译和实际画面验收；普通构建不会自动完成这些还原工作。

## 许可

原创代码、脚本与文档采用 [MIT](LICENSE)。游戏角色、模型、贴图、动画、场景、头像、UI 图像及其转换资产不属于 MIT 授权范围；中文字体使用 OFL 1.1。详见 [第三方资源说明](THIRD_PARTY_NOTICES.md)。本项目与原游戏开发、发行方无隶属或背书关系。
