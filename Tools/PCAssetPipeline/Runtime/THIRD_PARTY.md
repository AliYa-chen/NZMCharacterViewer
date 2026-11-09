# Runtime 第三方来源

此目录的原创脚本使用仓库根目录的代码许可。下载依赖、原游戏资源及它们的转换结果各自遵循其原有许可，不因脚本许可而改变。

## CUE4Parse 与 CUE4Parse-Conversion

- NuGet 版本：`1.2.2.202611`。
- 源仓库：[FabianFG/CUE4Parse](https://github.com/FabianFG/CUE4Parse)。
- 固定提交：[e76778c10ed25860e11ec488750753224d067d87](https://github.com/FabianFG/CUE4Parse/tree/e76778c10ed25860e11ec488750753224d067d87)。
- 许可：Apache-2.0，保留于 `LICENSE-CUE4Parse.txt`。
- 上游归属与所用第三方项目说明保留于 `NOTICE-CUE4Parse.txt`。

每个 NuGet 包的版本、官方下载 URL、SHA256、作者、仓库与原始 `license`／`licenseUrl` 元数据均在 `runtime.lock.json` 中。文件型许可（例如包内 `LICENSE`）会随完整归档展开到缓存。117 个包的许可不能统一视为 Apache-2.0；核对某个包时应读取其原始 `.nuspec` 与随包许可文件。

## Oodle 原生解码库

固定下载为 [WorkingRobot/OodleUE 2026-06-04-1357](https://github.com/WorkingRobot/OodleUE/releases/tag/2026-06-04-1357) 的 `clang-cl-x64-release.zip`，该地址来自固定版本 CUE4Parse 的 `OodleHelper`。

- 原归档保留在本地缓存，名称 `oodle-win-x64.zip`。
- 使用成员：`bin/oodle-data-shared.dll`。
- 归档与 DLL 的 SHA256 都固定在锁文件中。
- 本仓库不附带此第三方二进制。其适用许可应查阅上游 OodleUE 分发及 Oodle／Unreal Engine 的许可资料。

Skia 原生库来自锁定的 `SkiaSharp.NativeAssets.Win32` NuGet 包，其原归档和许可同样保留在缓存。
