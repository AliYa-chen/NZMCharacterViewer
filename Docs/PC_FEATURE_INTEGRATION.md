# PC 着色与原生布料接入

本轮把 PC 高清服装的皮肤、头发和已导出布料数据接入可执行的 UE 渲染／模拟路径。查看器继续使用原有 Catalog、待机、头像、眼部覆盖和环境；新增材质位于 `/Game/PCAssets/ViewerShading`，布料网格副本位于 `/Game/PCAssets/ViewerCloth`。原生转换库的网格、贴图和材质不改写。

## 着色范围

72 个高清部件引用 42 个皮肤材质和 17 个头发材质。新皮肤母材质使用原生 Subsurface Profile，头发使用原生 Hair 着色模型。基础颜色、法线、已确认的透明度与来源参数通过独立实例接入；用于布料的材质启用并读回 `UsedWithClothing`，黑寡妇手套另建一个保持原表面参数的 Default Lit 实例。原先的虹膜与 EyeOcclusion 修正按原槽位叠加保留。眼睛继续使用已经核对的径向 UV／Default Lit 适配；来源缺少完整角膜法线、光学深度与折射运算，不能仅切换 Eye 枚举就称原私有眼球 BRDF 已恢复。

来源只保存了部分私有 MaterialFunction 对象，关键输入、输出和运算连接已剥离。因此这里是有来源依据、能实际编译和绘制的原生 UE 重建；缺失的私有 BRDF 运算没有因换用同名着色模型而恢复。未确认的 PSAR／AIRR 通道不猜测连接。

15 个被引用的 SSS profile 中只取得 3 个定义；这 3 个保留各自来源参数，其余引用使用明确记录的全局 profile 兼容项。旧 ScatterRadius 与 UE5 参数的转换、范围截断、头发切线及透射参数的适配都由准备脚本逐项记录，不能据此声称与原私有着色逐像素一致。

## 布料范围

来源包含 9 个网格的 25 组 Nv 布料对象，其中 7 个网格出现在当前高清组合。桥接读取实际物理顶点、法线、三角形、骨骼权重、最大位移固定图、Backstop 与可用的 AnimDrive 数据，为运行部件的 19 组记录创建原生 ClothingAssetCommon／Chaos 配置，其余 6 组留作来源证据；对应渲染 section 绑定到布料模拟。网格副本沿用原 Skeleton 和材质槽，已有动画无需再适配。

运行中绑定的是 7 个 LOD0 渲染 section，其余已保存的来源低 LOD 物理网格留在网格副本中，不冒充已转换的渲染 LOD。来源最大位移图完整保留，但 Chaos 把小于 `0.1 cm` 的顶点按运动学点处理；源 inverse mass 同样保留，运行质量由显式记录的 Chaos Density 规则生成。

当前导出没有角色 PhysicsAsset 的碰撞形状内容。兼容碰撞体从同一套服装的身体／头部几何拟合，只在骨骼名称和参考变换一致时使用。外包围胶囊会把紧贴身体的布料推离原位移范围，因此保留其中心和方向，将半径与轴向长度收为拟合值的 `0.5`，形成明确标注的内部碰撞近似；它们没有被标为原始碰撞。旧 Nv 参数通过 UE 的迁移接口转成 Chaos；缺少的构造参数、求解器差异和未接入运行的来源分支单独列在报告中。

最终桌面采样中最大动态偏移约 `2.14 cm`，逐顶点相对于源位移图的最大超出约 `1.57 cm`，说明兼容碰撞与 Chaos 的约束顺序仍与原 Nv 不同；固定点最大误差约 `0.000039 cm`。这些值随采样、姿态与平台变化，不是原版逐帧一致的证明。

“发丝与衣物动态”设置同时控制现有骨链和原生布料。关闭时使用正常蒙皮，重新开启、重播及重置视角时重置布料状态，切换服装仍由原资源加载流程负责。原 DynamicBone 私有求解器与 Chaos 的算法不能视为相同。

## 重复执行

普通运行只依赖提交的 `Content`。需要重建时，先从已发布的 Git 来源快照恢复选定 JSON 缓存，不必恢复整个 `PCResources`：

```bash
python3 Scripts/export_pc_feature_sources.py
python3 Scripts/build_pc_viewer_cloth_plan.py
```

编译 Editor 模块后，在 UE Python 中依次运行 `prepare_pc_viewer_shaders.py`、`prepare_pc_viewer_cloth.py` 和 `apply_pc_feature_integration.py`。准备脚本只创建独立资产，最后一步才按角色／服装／部件 id 更新现有 Catalog 的网格和材质引用。

真实 Metal shader 检查使用 `prepare_pc_viewer_shaders.py` 的 `-PCViewerShadingVerifyOnly` 模式，需添加 `-AllowCommandletRendering`，不加 `-nullrhi`。`-ViewerSmokeTest -ViewerPCFeatures` 只选择已绑定原生布料的服装，检查实际粒子数据、有限位置／法线、连续运动及动态开关和重播。

最终验证：Mac Editor 与 iOS arm64 编译／链接通过；真实 Metal SM6 的 63 个材质 shader map 全部通过；6 套服装中的 7 个布料部件完成实际求解、固定点、动态开关和重播检查，六张运行截图已逐张检查。依赖审计的 7,646 个包全部可达，缺失、未知和闲置包均为零。保留的 6,918 个原转换／眼部／动画包哈希一致。

[验证归档](PCFeatureIntegration/integration_evidence.tar.gz) 包含报告、来源指纹与六张截图，[清单](PCFeatureIntegration/evidence_manifest.json) 包含逐文件哈希。shader 编译、桌面运行、iOS 编译和原游戏画面对照分别记录；本轮未追加移动预览、iOS cook、签名或真机测试。
