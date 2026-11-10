# 第三方资源说明

## 游戏资源

`Content/Characters`、`Content/PCAssets`、`Content/Viewer` 以及 [PCResources 历史源快照](https://github.com/AliYa-chen/NZMCharacterViewer/tree/03152adb7ce937649f890455d6650f346e82acc1/PCResources)中基于《逆战：未来 / Assault Fire Future》游戏资源转换的模型、骨架、贴图、动画、场景、头像、UI 图像和序列化参数，版权归各自权利人所有，不纳入本仓库 MIT 许可。

仓库的 MIT 许可只覆盖作者拥有版权的原创代码、脚本与文档；不表示授予游戏资产的再分发或商业使用权。`Content` 的重建资产使用新工程的可编辑 UE 格式；`PCResources` 曾提供 PC 端转换出的 PNG、ActorX 模型及参数 JSON，供单独导入和核对。完整导出在全部转换及最终核验通过后从工作树清理，源文件仍可从上述固定历史提交恢复。未包含原游戏可执行程序或原始 cooked 资源包。

PC 资源的离线解析使用 [CUE4Parse](https://github.com/FabianFG/CUE4Parse) 及其 Conversion 工具，版本与来源记录于[历史资源清单](https://github.com/AliYa-chen/NZMCharacterViewer/blob/03152adb7ce937649f890455d6650f346e82acc1/PCResources/source_manifest.json)；其工具采用 Apache-2.0 许可。`Tools/PCAssetPipeline/Cipher/Upstream` 保留实际使用的上游源码及 Apache 许可／NOTICE；其他依赖的固定版本、校验值和许可证元数据随工具安装脚本提供。下载的二进制、依赖缓存及本机日志保存在忽略目录，不作为查看器运行依赖分发。工具的许可不改变游戏资源的权利归属。

## Noto Sans CJK SC

文件：`Content/Slate/Fonts/NotoSansCJKsc-Regular.otf`，Noto Sans CJK SC Regular，Version 2.004。

字体内记录的版权声明：© 2014–2021 Adobe (http://www.adobe.com/)。字体按 SIL Open Font License 1.1 提供，许可随文件保留于 `Content/Slate/Fonts/OFL.txt`，来源与校验信息见同目录 `SOURCE.json`。

上游：[Noto CJK](https://github.com/notofonts/noto-cjk)。

## Unreal Engine

工程依赖 Unreal Engine 5.8.4。引擎由使用者自行安装，适用 Epic 的许可；本仓库不包含引擎源码、二进制或安装包。
