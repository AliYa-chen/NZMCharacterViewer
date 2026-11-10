# PC 人物与环境源资源

2026-11-09 使用 CUE4Parse／Conversion `1.2.2.202611`、提交 `e76778c` 的 `GAME_AssaultFireFuture` 分支离线转换。原始人物／环境集合来自基础 `Content/Paks` 的 80 个包；新增 [女性角色集合](Characters/README.md) 来自 137 个基础／更新档案的统一离线 provider，分别保留来源，未混用同名旧资源。

这些是供后续转换／导入的源资源。**现有 `Content` 与 Catalog 没有被本次导出替换；本机没有 UE，尚未完成 UE5.8 导入、动画或画面验证。**

**人物光照／后期／特效优先的接手机器，请从 [PC 人物渲染还原交接](../Docs/PC_CHARACTER_RENDERING_HANDOFF.md) 开始。** 其中给出来源参数、模板继承、当前代码覆盖点、待实现桥接／来源模式、编译与画面验收顺序。

## 文件

| 路径 | 内容 |
| --- | --- |
| `Characters/` | 新增女性角色集合：309 个网格包、629 个材质包、1101 个纹理包及全部配置／依赖／校验证据；实际文件按 SHA256 去重，详见其清单 |
| `Textures/` | 194 张实际解码 PNG，包含高清及保留的完整路径候选；最高实际可提取 mip 为 2048×2048 |
| `Meshes/` | 36 个独立 LOD0 ActorX 网格，35 个 `.psk`、1 个 `.pskx`，对应当前 40 个部件的共享源 |
| `Metadata/` | 网格、贴图及原材质实例的序列化 JSON；材质含真实纹理参数、标量、静态开关与原母材质引用 |
| `Environment/Json/` | 9 张候选人物页地图与 5 张数据表的 552 个导出对象 |
| `Environment/scene_parameters.json` | 129 个灯光组件、7 个后期区域、7 个粒子组件，以及天光、雾和反射参数 |
| `Environment/Particles/` | 5 个粒子系统的 125 个 JSON，含 11 个 emitter、87 个 module、102 组 cooked distribution 参数 |
| `Environment/Dependencies/` | 补取灯光蓝图／模板依赖，含真实导出索引与逐属性继承来源 |
| `Lighting/postprocess_profiles.json` | 7 个后期 Volume 的 256 条字段／override 证据，区分桌面与移动 Bloom |
| `Lighting/pc_quality_settings.json` | 去除机器路径的历史 CVar 请求与保存的画质配置，不代表最终帧状态 |
| `Shading/` | 另补 20 个肤／眼／发依赖包的 393 个 JSON、3 张支撑 PNG及实现缺口报告 |
| `Environment/HDR/` | 6 个 Radiance HDR 全景，来自实际 128×128／面的 FloatRGBA cubemap |
| `Environment/HDRMetadata/` | HDR 源纹理元数据；引用与转换说明在 `Environment/hdr_sources.json` |
| `texture_comparison.json` | 当前 244 张贴图与 PC 实际可用 mip 的逐项比较、完整路径候选与限制 |
| `texture_bindings.json` | 当前材质的纹理引用到同名 PC 材质具体参数的证据链；100 个更高清建议中 93 个通过此项核验 |
| `upgrade_manifest.json` | 仅含上述 93 个绑定核验通过的高清纹理候选，附目标与源 PNG 的 SHA256；未在 UE 执行 |
| `mesh_comparison.json` | 当前 40 个部件与 PC 各 LOD 的统计及原始网格映射 |
| `material_slot_references.json` | 原网格槽到原材质包的 162 条引用 |
| `export_validation.json` | PNG 与 ActorX 的 230 项实际导出验证 |
| `source_manifest.json` | 工具版本、来源包、源对象元数据和全部文件的 SHA256 |

## 已核实的差异

当前 244 张角色贴图的导入源最大为 1024×1024。保守比较得到 100 张 PC 可用分辨率更高、42 张相同、0 张更低、64 张缺少对应／测量、38 张具有多路径歧义。不能只按 `_HD` 名称或原 `ImportedSize` 判断可提取分辨率：例如 Naimei 披风法线的导入元数据为 2048，但 PC cooked 最大可用 mip 实际为 1024。

40 个现有网格中，29 项的 PC LOD0 三角数更高，11 项相同；多个部件共享同一 PC 包。当前统计来自可编辑资产的 AssetRegistry，PC 统计来自实际解码缓冲。FBX 导入会拆分顶点，因此顶点数并非同一统计口径，三角数相同也不能单独证明来源 LOD 相同。

Angela 头部是一个明确例子：当前为 13,880 三角，PC LOD0 为 14,116 三角、PC LOD1 为 13,880 三角；PC 基色是 2048×2048，当前为 1024×1024。原骨架、槽数与当前重建网格存在差别，不能直接套用现有骨架或槽索引。

## 导入与参数使用

PNG 保存实际最高可用 mip，没有放大。法线的 R/G 通道保留，BC5 的 Z 通道重建；没有翻转绿色通道或图像轴，也不是 glTF 烘焙法线。使用当前工程的纹理材质方式时，保留原 `sRGB`、法线压缩和 `Flip Green Channel=false` 等设置，并核对具体材质参数。

93 张通过绑定核验的高清纹理可先独立检查：

```bash
python3 Scripts/import_pc_textures.py --validate-files
```

然后在已编译的 UE5.8 工程中运行 `Scripts/import_pc_textures.py`，默认 `audit` 只读；命令行附加 `-PCTextureMode=apply` 才执行定点重导入。脚本先核验全部输入并备份所有目标包，保留当前纹理设置和 metadata，报告写入 `Saved/PCAssetAnalysis/texture_import_report.json`。此编辑器应用路径尚未在本机执行。应用不是原子事务；若中途失败，需要关闭编辑器并用报告中的完整备份恢复目标文件。导入器管理的源 PNG 路径会保留，PNG 随仓库提供。

ActorX 的点坐标、面数、骨骼数及每条非零权重／骨索引已对照原解码数据通过检查，包含超过四权重的顶点。其坐标采用 ActorX 导出约定（含 Y 镜像）。ActorX 是中间源格式，需用兼容的转换器生成 FBX，再核对单位、坐标轴、骨架、槽映射与待机动画。此次未提供经过 UE 验证的网格自动替换。当前所用 glTF 导出路径限制四权重，不适合保留这些网格的全部蒙皮数据。

HDR 全景由六面 cubemap 重投影为 443×221，保留 HDR 范围，但涉及投影重采样和 RGBE 量化；它不是原 cubemap 字节的无损封装。

地图参数保留相对变换、父链、模板和原属性。缺少的模板继承、引擎默认值、运行时场景选择与最终世界变换均有明确状态，不应把未合成的相对位置直接当作世界坐标。粒子报告保留 cooked distribution 的采样／查表数据，不声称恢复原编辑器曲线或运行效果。皮肤的 `M_Skin_Master_PC` 等母材质引用不包含原自定义着色器的实现。

## 文件完整性

从仓库根目录运行，无需 Unreal Engine 或原游戏：

```bash
python3 Scripts/validate_pc_resources.py
```

脚本验证清单、文件长度、SHA256、PNG／ActorX／HDR 头和文件集合；女性角色集合另检查全部计划网格、依赖、配置证据哈希与数据验证覆盖。通过只表示发布资源完整，不代表 UE 导入、骨架兼容、动画或画面已通过。

游戏资源的权利归属见 [第三方资源说明](../THIRD_PARTY_NOTICES.md)。本目录不含游戏可执行程序、原始 Pak、解密工具或本机日志。

实际使用的离线分析、提取、导出工具与固定依赖安装入口位于 [Tools/PCAssetPipeline](../Tools/PCAssetPipeline/README.md)。
