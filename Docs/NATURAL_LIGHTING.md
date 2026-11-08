# 自然人物光照校准

当前实现由查看器独立编写，参考角色专用光照、环境共同主光、克制后期与按材质控制反射的制作方法。移动和 Mac 共享环境参数与人物材质。这里没有集成 Enlighten、原游戏自定义 shader 或新第三方插件。

## 已实现

标准展台保留原 HDR 与场景几何，重新标定现有三盏 Spot：前侧主光 80 lm、较弱补光 25 lm、后侧轮廓光 80 lm，使用逆平方衰减。主光投影，补光和轮廓光不增加第二套投影。扩大光源形状用于控制高光尺寸；这不代表移动阴影具备 PCSS 面光源软影。

主光与轮廓光使用照明通道 0，补光仅使用通道 1。人物全部骨骼部件接收 0+1，场景仅接收 0，因此补光不额外照亮地面。这是角色直射补光，不是全局光照；HDR 反射、天光和 AO 不受该通道隔离。

Queen、Lucia、Naimei 的灯光、天光颜色与 HDR 强度继续保留。四个环境共同使用 AO 强度 0.45、世界半径 15 cm、温和 Bloom 0.08 和不超过 0.18 的暗角，取消额外中间调对比。运行时保留这些环境数值。方向灯的 CSM 覆盖集中到 16 m 和最多 3 级，实际级联数受画质设置约束。

当前 Catalog 中 25 个符合条件的皮肤／眼球材质完成标定：皮肤 Specular 0.35，保留原有粗糙度；眼球粗糙度 0.045。开启几何法线曲率粗糙度过滤，用于抑制尖锐几何高光。原颜色、法线贴图和发片透明方案继续使用。没有新增皮肤散射、发丝方向性 BRDF、折射或基于未知通道的粗糙度贴图。

## 重复应用

先正常编译工程，再在 Unreal Python commandlet 中执行；脚本默认只审计，`apply` 才写资产。

```bash
UE_EDITOR="$UE_ROOT/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor"
"$UE_EDITOR" "$PWD/NZMCharacterViewer.uproject" -run=pythonscript \
  -script="$PWD/Scripts/calibrate_viewer_lighting.py" \
  -LightingCalibrationMode=apply -nullrhi -unattended -nosplash
"$UE_EDITOR" "$PWD/NZMCharacterViewer.uproject" -run=pythonscript \
  -script="$PWD/Scripts/refine_character_surfaces.py" \
  -SurfaceRefinementMode=apply -nullrhi -unattended -nosplash
```

`nullrhi` 仅用于参数编辑和保存，画质必须另开真实渲染窗口验证。重新导入源资源后，需要重新应用这两项校准。

灯光支持 `-LightingKeyLumens`、`-LightingFillLumens`、`-LightingRimLumens` 等参数；材质可用 `-SurfaceRefinementFilter=angela,molly` 限定角色。脚本只处理明确的现有资产和输入类型，不确定的材质会跳过并记录原因。

## 恢复与边界

首次应用会将原二进制包与 SHA256 清单保存到 `Saved/LightingCalibration/OriginalContent` 和 `Saved/SurfaceRefinement/Original`。关闭该工程所有编辑器和查看器后，可用普通 Python 执行各脚本的 `--restore-files`。恢复先验证完整清单，再替换原件。

本地 Mac Metal ES3.1 预览不等同 iPhone／iPad 实机。当前 TAA 仍需真机确认整个人物闪烁；MSAA 与 Metal 后处理采样类型不匹配的问题仍需单独处理。编译、资源加载、截图存在与画质一致性是不同验证范围。

## 本轮验证

2026-11-08：Mac Editor 与常规 iOS Development arm64 编译／链接通过；Metal ES3.1 实际移动渲染窗口完成九套服装回归，9/9 通过。独立 TAA、SSR 关闭的 ±45° 相机旋转采集完成，保存 47 张截图；最终两轮日志未出现 Metal 纹理类型 ensure。检查 634 个工程资源包均可达，无缺失或未使用资产。原始报告位于本机 `Saved/LightingDebug/NaturalLightingVerification.json`。截图是稀疏采集，不能排除逐帧闪烁；iOS 真机尚未验证。

## 公开技术依据

- [Epic：无限暖暖开发者访谈](https://www.unrealengine.com/developer-interviews/behind-the-scenes-of-infinity-nikki-tracing-a-glamorous-turn-to-an-unreal-open-world?lang=zh)：角色自阴影、暗部 GI 调节及定制色调映射。
- [Silicon Studio：Enlighten 采用公告](https://www.siliconstudio.co.jp/en/news/pressreleases/2025/250206InfinityNikki/250206InfinityNikki.html)：跨移动与桌面平台的 GI 创作思路。当前查看器没有启用该中间件。
- [Epic：Creating Human Skin](https://dev.epicgames.com/documentation/en-us/unreal-engine/creating-human-skin?application_version=4.27)：皮肤反射率校准依据。
