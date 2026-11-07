# 角色页面部动画核对

2026-11-07 核对结果：普通角色页的创建路径默认请求 `RoleIdleFace` 面部状态。Angela 的该状态引用与身体待机相同的原动画包；目前没有依据把另一个 `FaceIdle` 动画直接覆盖到她的头部。嘴部亮斑／孔洞与原游戏表情差异的原因仍未确定。

本文面向维护动画导入与查看器的开发者，只记录资源标识、必要的配置值和分析结论。原游戏反编译 Lua、导出的完整配置与截图留在本机忽略目录 `Saved`，不作为本文的分发附件。下述静态链路可以证明配置连接，不能单独证明原游戏运行时最后发布的骨骼姿势。

## 已核实的链路

| 环节 | 核实结果 |
| --- | --- |
| `NewCharacterMainView.CreateActor` | 设置 `mLookAtMode = LobbyEnum.LobbyFaceAnimIndex.RoleIdleFace`；请求登场演出，之后进入普通角色页待机。 |
| `LobbyEnum.LobbyFaceAnimIndex` | `NoneFace=0`、`IdleFace=1`、`LobbyFace=2`、`PersonalityFace=3`、`RoleShow=4`、`LobbyIdle2Stand=5`、`LobbyStand2Idle=6`、`TeamComeIn=7`、`RoleIdleFace=8`。 |
| `BP_NZAnim_UIAnimBP` 的面部混合 | `AnimGraphNode_BlendListByInt_DDD6AFCA41A5805C35C1CCAC97A5AB74.ActiveChildIndex` 从 `LocalProxy.FaceAnimIndex` 读取。 |
| 索引 8 | `BlendPose[8].LinkID=245`，连接生成图的 `AnimGraphNode_SequencePlayer_3A699DB840728FCB09462B9D1EE715EE`。 |
| 动态资产绑定 | 该播放器的 `Sequence` 由 `EvaluateGraphExposedInputs.CopyRecords` 从 `AutoProp_FaceRoleIdle` 赋值。 |
| 资产集 | 在 `UIAnimSet_Face_All` 中按角色规则选择 `KeyName=FaceRoleIdle`。 |

生成类的状态机播放器索引与序列化图中的 `LinkID` 并非同一个排列；上述面部链路按图链接和实际属性复制记录核对，不能仅按数组倒序猜测。

## Angela 的两个不同引用

`UIAnimSet_Face_All` 的 `Angela3` 行：`KeyName=FaceRoleIdle`，`Rule=RoleId=10002000046`，引用：

```text
/Game/Avatar/Character/Angela/LobbyRes_Angela/Empty/SA_Char_Angela_Normal_KS
```

该包同时是 `UIAnimSet_Female_Common` 中 Angela 普通角色页身体 `RoleIdle` 的引用。

`Angela1` 行的 `KeyName=FaceIdle` 引用另一个包：

```text
/Game/Animations/Show/Face/SA_Char_Angela_Normal_KS
```

文件名相似不代表上下文相同。未观察到原游戏把普通角色页的面部状态 8 改成状态 1，因此没有把第二个包当作已验证的修复覆盖到头部。

其余已核对的 `FaceRoleIdle` 配置为：

| 角色 | 原游戏虚拟包路径 |
| --- | --- |
| Molly | `/Game/Animations/Show/Face/Char_Molly_Normal_3P_KS_Face` |
| Nanami | `/Game/Animations/Show/Face/SA_Char_Nanami_Lobby_Normal_KS` |
| Naimei | `/Game/Avatar/Character/Naimei/Naimei_Suit_A/Face/SA_Char_NaiMei_Lobby_Idle_00` |
| Angel | `/Game/Animations/Show/Face/Char_Angle_Normal_3P_KS_Face` |

这些引用说明可能需要原面部层，尚不等于已验证了它们与当前导入头部骨架的混合范围、姿势与材质效果。

## 当前问题与后续验证

原游戏中还有注视目标、头部／眼球限幅、角色姿势修正及 `bFixWink` 等材质分支。查看器目前没有完整恢复这些机制。不能仅凭嘴部亮斑或头部角度不同，认定问题一定来自面部动画层。

后续应在相同动作时间和视角下，分别核对身体、头部、口腔和眼部的骨骼姿势与材质。对候选面部动作的验证应只作用于经过确认的面部后代骨骼，保留已验证的身体动作、人物与家具对位；验证完成前不替换运行时 Catalog 引用。注视配置应先取得原始骨骼名单、权重和限幅，再实现相应控制。

本机核对记录包括 `Saved/EntranceImport/logic/LobbyEnum.disasm.txt`、生成动画蓝图的 JSON、`UIAnimSet_Face_All` 配置及 `Saved/RoleIdleCurveImport.json`。这些本机输入不属于普通克隆、编译或运行的必要条件。

## Angela 面部功能覆盖

| 功能 | 原资源证据 | 当前覆盖与缺口 |
| --- | --- | --- |
| 头部骨架 | 当前导入头部有 113 根骨骼，其中按 jaw、facial、lip、eye、brow 等名称分类得到 62 根面部／眼口骨骼。身体部件有 125 根骨骼，未包含这些面部骨骼。 | 骨骼动作按各部件现有 Skeleton 导入；身体和头部不是可随意互换的同一骨架。 |
| Morph Target | 对当前 Angela 五个运行部件逐个读取 `morph_targets`，均为空。 | 不能把任意 `CTRL_*` 浮点曲线直接当作已存在的 Morph Target。 |
| 演出面部控制曲线 | `Char_Angela_01_girl_cam003` 与 `cam006` 各列出 8 条眼部 `CTRL_*` 压缩曲线；`cam005` 列出 9 条，包含下颌；`cam007` 列出 32 条，包含眉、口、下颌、眼部。 | 当前稠密动画导入桥只处理骨骼平移／旋转／缩放，尚未恢复这些浮点曲线及其到原面部骨骼的驱动。不能声称骨骼片段导入等于完整面部系统恢复。 |
| 注视目标与限幅 | `Character_RoleShow` 枚举值为 204。Angela 的 204 配置启用注视，目标为 `(-10,200,102)` cm，身体限幅 `(15,15)`，眼球限幅 `(1,5,15)`；旋转时不启用注视。 | 已确认配置，未实现原骨骼权重、轴向、平滑与约束求解。202 是 Preset 配置，不能混用于角色展示。 |
| 眼球高光／反射动画 | `Angela_Show_cam03` 的头部材质索引 2 有 `FakeLightOffsetX/Y/Z`、`FakeLightIntensity`、`FakeLightGloss`、`EnvLightColor`、`FakeLightSpecularColor`；`cam006` 有 `HDRAngle`、`HDRAngleXZ`、`HDRAngleYZ` 键。 | 当前眼材质是静态 stock UE 图，没有这些原自定义参数的等价方程，尚未重放原眼部材质轨道。 |
| 皮肤材质动画 | `cam003` 与 `cam005` 的原头部材质索引 7 有 `Flatness (S)` 与 `RoughnessIntensity`。 | 原材质槽索引需要按原 SkeletalMesh 的槽表映射；当前导入头部只有 6 个槽，不能直接以原索引 7 设置。 |
| 演出中的毛发材质切换 | `cam003` 对 `AvatarSkeletalMesh_Hair` 指定 `VMI_Char_Angela_Suit_B_Hair_FirstPass_UI_Opt`；该 MI 明确覆盖为 Masked、CustomLit，`MaskClip=1.5`。普通 MI 是 Translucent、CustomLit，`MaskClip=1.0`。 | 已确认存在不同原材质分支。失败的 stock Hair 试验不等于原 Opt 着色器；试验资产和绑定已恢复、清除。 |
| 闭眼法线修正 | 原头部 MI 声明 `bFixWink=true`，并提供 `WinkNormalMap`。 | 当前没有原闭眼法线混合方程与动态参数连接；不能仅凭名称猜测触发规则。 |

上述控制曲线名称、动画片段与材质轨道来自相应包的序列化记录。曲线值解压、面部驱动、材质方程与真机画面仍应分项验证。当前嘴部亮斑／孔洞的根因未定，不能因为发现某个缺口就直接宣布它是根因。
