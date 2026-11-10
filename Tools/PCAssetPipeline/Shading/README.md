# 角色材质依赖证据

此工具复用相邻 `Runtime` 的固定依赖缓存，仅离线读取资源文件。它不使用游戏运行、进程枚举、内存、调试、注入或反作弊接口。

以下为可选的重新提取与证据整理流程。完整导出在全部转换和最终核验通过后从工作树清理；原结果见 [Shading 历史快照](https://github.com/AliYa-chen/NZMCharacterViewer/tree/03152adb7ce937649f890455d6650f346e82acc1/PCResources/Shading)。需要使用既有元数据时先按[上级说明](../README.md)恢复源快照。查看器运行使用已保存的 `Content`。

`dependency_seeds.json` 固定 **20 个 package**：7 个当前角色共享父、1 个发光眼球的实际父、6 个皮肤／头发／眼球函数、3 个代表性 SSS profile、3 个由这些元数据明确引用的支撑纹理。它不是完整依赖闭包，也不会仅凭名字把贴图认作 LUT。

```powershell
& ./Tools/PCAssetPipeline/Runtime/Prepare-Runtime.ps1

& ./Tools/PCAssetPipeline/Shading/Collect-Dependencies.ps1 `
  -PakDirectory $localContentPaksDirectory -Publish
```

默认原始输出为 `Saved/PCAssetAnalysis/PCShadingDependencies/Export`。`-Publish` 会重新生成 `PCResources/Shading/Dependencies` 的 JSON／PNG 及来源清单，并结合已恢复的 `PCResources/Metadata` 中的 VMI，生成 `PCResources/Shading/readiness.json`。未传 `-Publish` 时只保留 Saved 输出。

恢复已提取的源证据后，如仅需重新整理：

```powershell
& ./Tools/PCAssetPipeline/Shading/Build-Readiness.ps1
```

导出失败会停止发布。来源清单只保存虚拟路径和游戏内相对 Pak 路径、偏移、大小与 SHA256，不带调用者安装路径。

实际结果保留部分表达式列表、参数节点和函数引用，但根连接、部分节点与函数输入没有形成已验证的完整可编辑 graph。SSS profile 的旧引擎单位、默认值及当前 UE5 的等价性也没有验证。此工具不执行 UE 导入或 shader 编译。
