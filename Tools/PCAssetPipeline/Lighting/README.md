# 人物页灯光模板继承提取

这些是本次实际使用的离线入口。先按 `../Runtime/README.md` 准备固定版本缓存，再在仓库根目录的新 PowerShell 7.6 会话运行：

```powershell
$gameRoot = Read-Host 'NZM 目录（其下包含 Content/Paks）'
& ./Tools/PCAssetPipeline/Lighting/Export-LightingDependencies.ps1 -GameDirectory $gameRoot
```

默认读取 `PCResources/Environment/scene_parameters.json` 和 `Json` 中已经选定的九张地图。中间结果写入 `Saved/PCAssetAnalysis/PCLightingDependencies`；纯 JSON、来源清单和合并证据写入 `PCResources/Environment/Dependencies`。入口可传 `-EnvironmentDirectory`、`-WorkDirectory` 和 `-CacheDirectory`。缓存只由现有 Runtime 检查和加载，本入口没有网络下载操作。

`Get-TemplateSeeds.ps1` 收集地图对象明确的外部 `Template` 引用；`Find-TemplateClosure.ps1` 仅继续追踪 `Template`、`ComponentTemplate`、`Super`、`SuperStruct`、`ParentClass` 和 `ClassDefaultObject`。总量上限为 30 个依赖包、3 层。达到层数上限仍有引用时记录未完成项；超过包数上限则停止，不自动选取部分依赖。本次得到 8 个种子包、2 个父模板包，共 2 层、148 个 JSON，均成功解析。

`Export-ObjectIndex.ps1` 从 CUE4Parse `ExportsLazy` 读取真实的零基导出序号，以及 `UObject.Template` 的 `ResolvedObject.Package/ExportIndex/GetPathName`。`Resolve-Templates.ps1` 以包名、类、对象名和精确 `Outer` 序号关联原始 JSON；没有按模糊名称猜测引用。它仅沿实际 `UObject.Template` 链合并序列化属性，后代覆盖祖先，保留显式 `false`、`0`。`field_sources` 使用 JSON Pointer 转义键路径，记录每个字段相对于 `PCResources/Environment` 的来源。

`AttachParent`、相对变换、根组件和本包组件引用只保留地图实例中明确序列化的值，避免移植模板的组件序号。`effective_serialized_properties` 表示恢复出的序列化属性叠加，不能解释成最终运行时有效值。原生类默认值、构造脚本、运行时设置、最终可见性、世界变换和画面效果均未恢复。比如标准主光模板的 `10 Candelas / bAffectsWorld=false` 被地图实例明确覆盖为 `14 Lumens / true`，不能把模板直接当最终灯光设置。

`object_index.json` 保留索引身份和引用核对；`resolved_components.json` 含 129 个灯光、7 个后期区域、18 个相机组件、49 个场景组件及相关蓝图对象。`export_manifest.json` 给出包来源和 SHA256。含本机路径的 `export_report.json` 只留在 `Saved`，不复制到发布目录。工具不修改 `Content`、`Config` 或 Catalog，也不启动游戏、编辑器、枚举进程或读取内存。

完整入口最后调用 `Validate-Templates.ps1`，检查每个字段的 JSON Pointer 与源值一致、显式 `false` 保留、拓扑只来自实例、依赖 JSON 哈希和精确模板身份。也可单独运行该验证器；它不读取游戏目录，不代表已在 UE 中运行或验证画面。
