# NZMCharacterViewer

基于 Unreal Engine 5.8.4 的非官方离线人物查看器，面向 macOS 和 iPhone／iPad。当前工程包含 5 名角色、9 套服装、40 个运行部件及待机动画。

支持人物／服装切换、触摸与鼠标旋转缩放、视角复位、动画重播、隐藏界面、部件开关，以及保存画质设置。标准展台和部分服装场景已导入；Bianca 当前使用标准展台。

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

运行时按服装加载软引用资产，准备完成后切换显示。设置包含帧率、分辨率、阴影、抗锯齿、后期和动态效果；实际选项受设备能力限制。

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

## 验证状态与限制

- 清理后、不含 `SourceAssets` 的干净 Git 克隆已通过 Mac Editor 全量编译，UE 依赖闭包检查无缺失。
- 同一干净克隆已完成 iOS Development arm64 编译／链接；尚未完成完整 cook、签名、可安装 IPA 和真机验证。
- macOS Metal 在切换或画质调整测试中出现过 `IRBindUniformBuffer`／`SharedPointer IsValid()` 断言崩溃，根因未定，最新预加载逻辑仍需渲染回归。
- 材质使用 stock UE 重建。部分眼球、皮肤、毛发和动态效果为有记录的近似；原自定义 shader、粒子、完整布料与原游戏渲染没有完整恢复。
- iOS 17+ 为当前配置下限；帧率和画质选项不代表已验证的真机性能。

## 工程与资源维护

| 目录 | 内容 |
| --- | --- |
| `Source` | C++ 运行模块和构建目标 |
| `Config` | 游戏、输入、渲染、设备和打包配置 |
| `Content/Characters` | 角色、服装、动画、材质和贴图 |
| `Content/Viewer` | Catalog、地图、展台、场景和界面 |
| `Content/Slate/Fonts` | 中文字体、来源与 OFL 许可 |
| `Scripts` | 构建、检查、可选重新导入与设备流程 |

离线预览、原始转换输入、未使用资产和构建输出已从工程中清理。贴图的本机重导入路径已清除，283 张贴图经新进程加载核对，原始 mip 像素哈希、源 ID 和压缩／色彩设置保持一致。资源清理依据运行时入口及 UE AssetRegistry 的硬引用、软引用和管理引用闭包，保留字体和代码动态加载的界面资产。可重复运行只读检查：

```bash
"$UE_ROOT/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor" \
  "$PWD/NZMCharacterViewer.uproject" -run=pythonscript \
  -script="$PWD/Scripts/audit_content.py" -nullrhi -unattended -nosplash
```

报告写入 `Saved/ContentAudit.json`；此检查只读取磁盘资产依赖，不验证画面或设备性能，也不会自动删除资源。新增通过代码动态加载的资源时，需同步维护脚本中的入口。

需要重新导入 FBX／PNG 时，见 [导入说明](Docs/ASSET_IMPORT.md) 和 [清单格式](Scripts/MANIFEST.md)。

## 许可

原创代码、脚本与文档采用 [MIT](LICENSE)。游戏角色、模型、贴图、动画、场景、头像、UI 图像及其转换资产不属于 MIT 授权范围；中文字体使用 OFL 1.1。详见 [第三方资源说明](THIRD_PARTY_NOTICES.md)。本项目与原游戏开发、发行方无隶属或背书关系。
