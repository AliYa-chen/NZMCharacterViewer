# 灯光模板与字段来源

本目录补充九张人物页候选地图的外部蓝图模板依赖。10 个包、2 层、148 份属性 JSON 来自 PC 基础 `Content/Paks`，未选择或挂载 `Saved` 更新包。

`resolved_components.json` 对 129 个灯光、7 个后期区域、18 个相机组件、49 个场景组件及相关蓝图对象逐项记录模板链和字段来源。`object_index.json` 使用真实 CUE 导出序号、`Outer` 序号核对对象身份；184 条模板引用全部匹配，695 个原始对象均精确关联，没有名称猜测。`export_manifest.json` 保存来源包与文件 SHA256；路径均相对于 `PCResources/Environment`。

合并值仅代表序列化属性叠加，原生默认值仍未知。显式 `false` 和 `0` 保留；实例覆盖模板。组件拓扑与相对变换只保留地图实例值，未移植模板的本包引用，未计算世界变换。构造脚本、运行时参数、可见性及最终渲染效果未验证。入口与限制见 [提取工具说明](../../../Tools/PCAssetPipeline/Lighting/README.md)。
