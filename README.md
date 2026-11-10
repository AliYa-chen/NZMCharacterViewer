# NZMCharacterViewer

基于 Unreal Engine 5.8.4 的非官方离线人物查看器，面向 macOS 和 iPhone／iPad。当前 Catalog 包含 **12 名角色、17 套 PC 高清组合**，另保留 9 套“旧版对照”，总计 26 套、112 个运行部件。每名角色默认选择高清组合；已有五名角色沿用原头像。

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

高清组合使用独立的 PC 来源光照模式，恢复有模板和实例依据的灯位、颜色、强度、单位、HDR 与桌面／移动后期 override。原查看器的固定曝光、景深、SSR 和调色常量不会覆盖这些组合。来源缺少在线场景路由、模型世界坐标或私有 shader 的部分会明确记录；Lucia、Bianca 专用灯光的定位尚未恢复，当前使用标准 PC 人物页灯光。原九套对照保留此前的自然光校准。完整范围见 [PC 高清角色接入](Docs/PC_VIEWER_INTEGRATION.md)，原校准流程见 [自然光校准](Docs/NATURAL_LIGHTING.md)。

Mac 的 Metal 配置支持 Lumen 与硬件光追；旧版对照沿用原查看器的 GI 设置。PC 来源模式未用 Lumen 代替未恢复的私有 PRT／GI，使用显式记录的标准材质与反射兼容设置。普通 iOS 使用移动前向渲染、TAA、屏幕空间反射与环境光遮蔽。跨设备判断光追是否生效时，应同时核实原生能力、UE RHI 和运行状态。

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

- 2026-11-10 高清接入完成桌面 Metal SM6 全部 26 套组合的加载、待机、切换、设置和部件检查；17 套高清截图逐套核对。Mac Editor 与 iOS arm64 编译／链接通过。本轮按要求停止追加移动／真机测试，并清理原始导出、40 个未引用资产包与生成缓存；证据见 [高清接入说明](Docs/PC_VIEWER_INTEGRATION.md)。
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
| `Content/PCAssets` | PC 高清模型／纹理、来源记录、适配待机和运行材质／环境 |
| `Content/Viewer` | Catalog、地图、展台、场景和界面 |
| `Content/Slate/Fonts` | 中文字体、来源与 OFL 许可 |
| `Scripts` | 构建、检查、可选重新导入与设备流程 |

离线预览、原始转换输入、未使用资产和构建输出已从工程中清理。取消过场后，运行时的 `Presentation`／`EntryAnimation` 引用和跳过按钮已移除；重新保存当前 Catalog 后完成依赖清理，累计 73 个不可达资产（约 167.5 MiB）已备份移至本机忽略目录 `Saved/Cleanup`。当次清理报告包含 634 个工程包，全部可达，无缺失、未知依赖或未使用资产；原 `RoleIdle` 待机资产与曲线继续保留。

贴图的本机重导入路径已清除，283 张贴图经新进程加载核对，原始 mip 像素哈希、源 ID 和压缩／色彩设置保持一致。资源清理依据运行时入口及 UE AssetRegistry 的硬引用、软引用和管理引用闭包，保留字体和代码动态加载的界面资产。可重复运行只读检查：

```bash
"$UE_ROOT/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor" \
  "$PWD/NZMCharacterViewer.uproject" -run=pythonscript \
  -script="$PWD/Scripts/audit_content.py" -nullrhi -unattended -nosplash
```

报告写入 `Saved/ContentAudit.json`；此检查只读取磁盘资产依赖，不验证画面或设备性能，也不会自动删除资源。新增通过代码动态加载的资源时，需同步维护脚本中的入口。

需要重新导入 FBX／PNG 时，见 [导入说明](Docs/ASSET_IMPORT.md) 和 [清单格式](Scripts/MANIFEST.md)。

PC 提取的模型、高清纹理、HDR 及人物页灯光／后期参数已转换为可编辑 UE 资产，并接入查看器。完整原始导出可从已发布的 [PC 资源快照](https://github.com/AliYa-chen/NZMCharacterViewer/tree/03152adb7ce937649f890455d6650f346e82acc1/PCResources) 恢复；普通打开、编译和运行不依赖根目录导出文件。离线工具及固定版本依赖安装入口见 [PCAssetPipeline](Tools/PCAssetPipeline/README.md)，来源差异见 [PC 端资源分析](Docs/PC_ASSET_ANALYSIS.md)。

安装内女性角色的 [原始源集合说明](https://github.com/AliYa-chen/NZMCharacterViewer/blob/03152adb7ce937649f890455d6650f346e82acc1/PCResources/Characters/README.md) 覆盖 309 个骨骼网格包、629 个材质包和 1,101 个纹理包。运行组合按真实 `UIConfig` 部件清单生成，不把资源包数量当作服装数量。Bunny 耳饰和七实腿部挂件按骨骼挂点运动；女王蛇使用专用待机，球保留参考姿势；艾玛缺失的来源部件会明确标注。

原生转换库共有 6,833 个资产，其中 14 个独立环境候选位于 `Content/Viewer/PCEnvironment`，其余位于 `Content/PCAssets`，编辑器入口为 `/Game/PCAssets/Collections/DA_PCAssetLibrary`。运行接入另建待机、眼部覆盖、动态子集与 PC 环境，保持原库索引字节不变。完整索引见 [PC 资源转换](Docs/PC_ASSET_CONVERSION.md)，运行验证和已知差异见 [高清接入说明](Docs/PC_VIEWER_INTEGRATION.md)。新角色使用明确标注的共享待机，私有 Hair／Eye／SSS、面部、粒子和完整布料仍有缺口。

后续机器优先恢复人物受光、后期与特效时，按 [PC 人物渲染还原交接](Docs/PC_CHARACTER_RENDERING_HANDOFF.md) 执行。文档汇总补取的灯光模板、后期 override、肤／眼／发材质依赖和当前代码中的覆盖点，明确哪些仍需实现、编译和实际画面验收；普通构建不会自动完成这些还原工作。

## 许可

原创代码、脚本与文档采用 [MIT](LICENSE)。游戏角色、模型、贴图、动画、场景、头像、UI 图像及其转换资产不属于 MIT 授权范围；中文字体使用 OFL 1.1。详见 [第三方资源说明](THIRD_PARTY_NOTICES.md)。本项目与原游戏开发、发行方无隶属或背书关系。
