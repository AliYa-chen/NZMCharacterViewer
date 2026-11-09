# 皮肤、毛发与眼球的还原证据

此目录新增 **20 个选定 package 的 393 个 JSON 对象和 3 张支撑 PNG**，来源为 PC 基础 `Content/Paks` 的 80 包。它们补充现有角色 VMI 参数与贴图，不修改工程 `Content`。

- [readiness.json](readiness.json)：按 skin／hair／eyes 列出 24／10／8 个 VMI 的明确参数、父材质、profile、LUT 观察结果和实现缺口。
- [Dependencies/export_manifest.json](Dependencies/export_manifest.json)：20 个来源 package、每项文件 SHA256、相对 Pak 位置与范围。
- `Dependencies/Metadata`：共享父、函数、SSS profile 及保留下来的表达式属性。
- `Dependencies/Textures`：明确引用的眼球中平面位移、头发细节法线、头发噪声；不是推测的 LUT。

这 3 张支撑纹理都记录为 `TC_Default/PF_DXT1`，`IsNormalMap=false`。其中 `T_Hair_Detail_Normal` 实际采样类型为 `LinearColor`、sRGB=false；不能仅因文件名含 Normal 就改成 UE Normalmap 压缩或自动翻转通道。Hair_Noise 的源 sRGB=true，眼球位移的源 sRGB=false，原值保存在清单中。

实际父材质记录为 `MSM_SubsurfaceProfile`、`MSM_Hair`、`MSM_Eye`。这些名称不能证明其在当前 UE5.8／Metal／移动路径中与原 PC 渲染等价。

表达式有残留，但不完整：普通眼球母材质有 258 个表达式列表项，其中仅 97 个非空引用／对象；头发母材质为 7 项中 5 项；皮肤母材质为 2 项中 1 项。所有已取样母材质的 `MaterialAttributes.Expression` 都为 null，只保留 `ExpressionName`。因此当前文件**不是可直接导入的材质图，也没有恢复私有 shader、完整函数连接或引擎实现**。

导出了默认皮肤、Angela 头部和身体的 3 个 SubsurfaceProfile。原始 `ScatterRadius` 分别为 1.8、1.5、5，`MeanFreePathDistance` 均为 2.6748；旧版本单位、默认值与当前 UE5 的 profile／kernel 语义尚未核实。其他角色的 profile 引用完整列入 readiness，并标记未在这次 20 包范围内导出，不能据此使用默认值补齐。

在已经检查的 VMI 和共享函数元数据中，没有观察到明确的 LUT／Preintegrated 纹理引用；这不能证明完整原着色器不使用 LUT。剩余依赖逐项标记为未导出，不能把参数表视为实现已就绪。

在另一台 UE 机器上，先按角色／部件逐项建立独立候选材质，保留材质槽、纹理语义与原始参数证据。补全并核验图连接、透明混合、法线方向、SSS／毛发／眼球实现后，再做 shader 编译和同视角 A/B。现有查看器使用过独立材质及面部校准，不能直接覆盖 Catalog。本目录没有 UE 编译、画面一致或设备验收通过的结论。

复现入口见 [Shading 工具](../../Tools/PCAssetPipeline/Shading/README.md)。
