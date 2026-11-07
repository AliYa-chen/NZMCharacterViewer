# Angela 登场灯光轨道研究归档

角色过场演出已按需求取消。运行时的演出引用、播放逻辑和跳过按钮均已移除，角色选择后直接进入普通待机；下述内容记录取消前的资源研究，不表示查看器仍播放这些镜头或灯光轨道。普通角色页使用的环境灯光与 `RoleIdle` 骨骼待机继续保留。

## 原资源采样结果

原演出长 22.733 秒，共 6 个镜头。研究中从原 MovieScene 记录采样了 19 条局部灯光轨道和 5 条 HDR 天光轨道。局部灯光模板包含 18 个聚光灯和 1 个点光源；点光源 `Point Light_vfx` 设置 `bAffectsWorld=false`、`bMobileDeferredOnly=true`，在前向路径试验中保持关闭。它附着于武器的 `b_Root`，未恢复该关闭光源的骨骼附件位置。

| 镜头 | 时间范围（秒） | 镜头起始可见局部灯 | 可见天光 |
| --- | --- | --- | --- |
| `Angela_Show_cam01` | 0–2.6667 | 4 | 1 |
| `Angela_Show_cam02` | 2.6667–4.3333 | 2 | 1 |
| `Angela_Show_cam03` | 4.3333–5.9333 | 4 | 1 |
| `Angela_Show_cam05` | 5.9333–7.6 | 4 | 1 |
| `Angela_Show_cam06` | 7.6–15.9333 | 4 | 1 |
| `Angela_Show_cam07` | 15.9333–22.7333 | 0 个镜头专用灯 | 0 个镜头专用天光 |

最后一个子镜头未设置专用演出灯光，试验中恢复普通角色页灯光。原首镜头组件模板的四个局部灯均写有 `Intensity=8`，但 MovieScene 在时间 0 的有效值依次为 `10、50、60、80`。组件模板经过轨道求值后才能代表该帧的配置；仅复制首镜头固定灯光不足以恢复整段演出。

天光在 `IBL_Cha_01_M` 与 `IBL_Studio_01_Mobile` 两个原 HDR 之间切换，并有不同的强度和颜色。试验采样包含每帧可见性、变换、强度、线性颜色、灯半径、光源半径和镜面比例，使用与相机、人物相同的 30 Hz 时间轴，共 683 帧；切镜时不跨镜头插值。

## 当前工程状态

原试验的 `UViewerPresentation` 运行时类型及演出导入脚本已移出活动源码。相关本机源码备份位于忽略目录 `Saved/CancelledPresentationSource`，采样输入留在 `Saved/EntranceImport`，均不是普通克隆、编译或运行的必要条件。

取消后，依据运行时入口的 UE 资产依赖闭包，将累计 73 个不可达资产备份移至 `Saved/Cleanup/UnusedAssets`。清理后的 `Saved/ContentAudit.json` 包含 634 个工程包，缺失包、未知依赖和未使用资产均为空。这是依赖完整性检查，不证明原游戏画质一致。

## 研究结论的范围

采样值可以说明原演出的时间轴配置，不能单独证明像素输出一致。原类继承的默认值、移动灯光归一化、ShadowVolume、PCSS、自定义皮肤／毛发／眼球 BRDF 和后期处理尚未完整恢复。普通角色页的面部混合与注视控制缺口见 [面部动画核对](FACIAL_ANIMATION_EVIDENCE.md)。

本机研究记录包括 `Saved/EntranceImport/angela_lighting_face_inventory.json`、`angela_lighting_track_report.json` 和 `angela_presentation_lit.json`。本文保留必要的资源标识和结论，未附原游戏反编译代码或完整配置。
