# PC 高清角色接入

人物查看器以 PC 导出资源为默认模型：12 名角色、17 套有 `UIConfig` 配置依据的高清组合、72 个部件。原有 9 套服装保留在各角色的高清组合之后，名称带“旧版对照”；总计可选择 26 套、112 个部件。角色顺序保留原有五名，再追加贝拉、芭比、艾玛、莉莉丝、雪莉杨、黑暗天使和黑寡妇。

每名角色默认进入第一套高清组合。原五名角色直接沿用已有头像；新增七名使用已经准备好的头像资源，无需继续重渲。兔耳和七实腿部挂件使用来源骨骼挂点，身体与挂件可以独立开关。女王的蛇保留共享坐标原点，使用原有专用待机，避免重复挂接导致偏移。

本轮桌面 Metal SM6 的 26 套加载、待机、切换、部件与设置检查全部通过；17 套高清组合的实际截图已逐套检查。Mac Editor 和 iOS arm64 编译／链接通过。按照本次用户要求，停止追加移动预览和真机测试；这些结果不包含本轮 iOS cook、签名或安装。

## 资产入口

- 运行 Catalog：`/Game/Viewer/Data/DA_ViewerCatalog`。
- 原生转换库：`/Game/PCAssets/Collections/DA_PCAssetLibrary`，包含全部转换模型、贴图、材质及编辑器来源记录。
- 查看器动画、头像、眼部材质与兼容动态：`/Game/PCAssets/Animations`、`Portraits`、`ViewerMaterials`、`Dynamics`。
- 最终数量、文件指纹、验证范围与清理记录见本目录 `PCViewerIntegration` 中的报告；原生转换库的独立索引仍在 [PCAssetConversion/asset_index.json](PCAssetConversion/asset_index.json)。

原生库的 6,833 个包保持转换验收时的字节和哈希。运行时眼部修正使用另建的材质覆盖，读取 PC 高清虹膜和巩膜纹理；它不修改已验收网格、纹理或原始材质候选。

## PC 光照与后期

高清组合使用独立的 PC 来源模式。它读取模板与实例合成后的灯位、颜色、强度、单位、光源尺寸、阴影、通道、HDR 和后期显式 override，桌面与移动 Bloom 分别处理。开启某项效果时保留来源值和 override 位；用户关闭效果时才禁用对应项。原查看器固定的曝光、Fstop、自动对焦距离、SSR 强度和调色常量不会再覆盖这些高清组合。灯光与模型各自保留原坐标，相机围绕整个场景旋转。

准备阶段生成 12 个环境资产，其中 5 个运行变体供 17 套组合使用；未接入的 7 个独立来源候选在清理时移出 Content，其参数与包字节保存在证据中。当前选择标准 PC 人物页灯光 14 套、Queen Preview 2 套、Nai_Mei 1 套。原九套对照服装仍使用原环境。Queen Preview 与 Nai_Mei 的女性相机标签、变换和镜头参数有明确来源；正常交互会为界面留白调整构图，使用 `-ViewerPCSourceCamera` 可在对照采集中保持来源镜头旋转和 FOV。

女王沿用原两套服装所有部件共同的王座对齐变换（平移 `22.698896, 79.521060, 0`，Yaw `45°`），整体只应用一次，来源部件的局部变换保持。三盏来源灯的 `SpecularScale=2/3/5` 超出标准 UE shader 的 `0..1` 范围，运行值明确适配为 `1`，来源值与引擎限制保留在报告中。

这些选择不能证明原在线版本的全部场景路由。所有 17 份 `UIConfig` 都没有逐角色场景路由或人物世界坐标；Lucia、Bianca 的专用灯光位于尚未恢复人物定位的世界坐标，因此运行时明确使用标准 PC 灯光，保留专用来源候选，未按灯的位置猜测人物偏移。PC 背景网格未在本次导出中提供，运行环境沿用已有可编辑展台几何。

另外几项兼容处理单独保留：来源静态灯因缺少光照构建数据而转为动态灯；来源曝光 `Min/Max=0` 的 fork 语义不能直接换成 UE5 EV100，因此目前使用明确标注的标准手动曝光；原私有 PRT／GI、Cubemap 归一化、微阴影、LightFunction、反射捕获及粒子未因导入参数而自动恢复。选用的画质 CVar 来自历史 PC 设置请求，实际运行读回会另行核对，不能把历史请求当成原游戏当前生效状态。

## 动作与动态

67 个部件使用已保存、重载并逐帧核对的待机动画；3 个局部附件通过动画父骨骼运动。67 份动画均检查了 91 个原始采样帧和 91 个压缩采样帧，共 533,988 个原始及同数压缩骨骼关键帧。最大原始位置／旋转误差为 `0.000211 cm / 0.000357°`，最大压缩误差为 `0.0411 cm / 0.425°`。

同款角色沿用已有待机并适配独立骨架；没有原动作的新角色明确显示“共享待机”。女王两套组合中的球部件没有可用专用动作，保留参考姿势并显示说明。艾玛缺少配置中的 `10061010001` 部件，组合与状态明确标为部分恢复，不推断缺失部件的用途。

原有 20 处动态绑定只在相同服装、相同部件上复用，并比较实际参与求解的骨骼顺序和父子关系：14 处完整复用，4 处生成完全匹配的骨链子集；Angela 的头发与帽子缺少全部旧骨链，明确标为不可沿用。不猜测骨骼改名，也不把其他人物的参数套给新角色。逐项原因和保存资产见动态报告。此处保留的是查看器已有的有限骨链求解，未恢复原私有 DynamicBone 求解器、碰撞或 Chaos Cloth。

眼部径向 UV 与标准材质响应属于有来源参数的近似。原 EyeOcclusion 辅助壳缺少私有 AO 方程，通用材质会把它画成白色遮挡层；运行覆盖将这层设为透明，实际 Metal 的相同姿态前后图确认虹膜与瞳孔恢复。后续已接入原生 Hair／Subsurface Profile 着色重建及有来源物理数据的 Chaos 布料，见 [着色与布料接入](PC_FEATURE_INTEGRATION.md)。原版折射、瞳孔控制、私有着色运算、面部混合、粒子及未导出的布料仍有缺口。皮肤／毛发关键函数缺少输入、输出及连接，无法仅凭材质枚举或 packed 贴图名字恢复；具体缺图数量与 SSS 单位边界保存在 shader 缺口报告中。

## 重复验证与可选重导入

普通打开、编译和运行只使用 `Content`，无需根目录原始导出。需要重做来源转换时，可从已发布的 [PC 导出快照](https://github.com/AliYa-chen/NZMCharacterViewer/tree/03152adb7ce937649f890455d6650f346e82acc1/PCResources) 恢复：

```bash
git restore --source=03152adb7ce937649f890455d6650f346e82acc1 -- PCResources
```

根目录 `PCResources`、未引用资源和生成缓存按本次要求清理。运行资产仍全部在 `Content`，无需恢复原始导出。清理列表见 [资源清理记录](PCViewerIntegration/content_cleanup.json) 和 [来源清理记录](PCViewerIntegration/source_cleanup.json)，不再使用的新头像与候选环境也在其中。

最终报告、截图、计划与原 Catalog 基线压缩保存在 [接入证据](PCViewerIntegration/integration_evidence.tar.gz)，[清单](PCViewerIntegration/evidence_manifest.json) 包含逐文件哈希。验证报告中的 `Saved/PCViewerIntegration` 是当时执行路径；需要重复来源审计时，先恢复相关归档输入，不要从更新后的 Catalog 重新生成“原版基线”。原生转换的完整计划位于 `Docs/PCAssetConversion/conversion_evidence.tar.gz`。被移出 Content 的 40 个未引用包另有小型字节归档，方便核对清理依据。

主要脚本按依赖顺序为 `build_pc_viewer_plan.py`、`attach_pc_viewer_animations.py`、`prepare_pc_dynamics.py`、`build_pc_viewer_eye_materials.py`、`extend_pc_viewer_eye_occlusion.py`、`prepare_pc_viewer_environments.py`、`render_pc_viewer_portraits.py`、`import_pc_viewer_catalog.py` 和只读的 `audit_pc_viewer_integration.py`。来源计划生成需要恢复原始导出；运行时审计只读取保存的计划、Catalog 和 UE 资产。眼部第二阶段报告包含虹膜与遮挡壳覆盖；不能只重跑第一阶段后把虹膜报告误当最终覆盖报告。

动画与头像需要 Editor 的 `AnimationData`、`ControlRig` 和 `RigVM` 支持。使用 `-NoEnginePlugins` 的精简命令时，必须显式启用这些插件，并保留 `ChaosCloth` 与输入设置依赖的 `EnhancedInput`；普通编辑器启动沿用引擎默认插件。头像和画面验证使用实际 Metal，不能加 `-nullrhi`。静态依赖、编译与动画数据验证不能代替实际画面、cook 或 iOS 真机检查。
