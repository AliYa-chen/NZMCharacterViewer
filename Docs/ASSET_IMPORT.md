# 可选的资产重新导入

仓库中的 `Content` 是完整的可编辑 UE 工程资产。普通克隆、C++ 编译、编辑已有材质／地图与运行查看器不依赖 `SourceAssets`。

离线提取、转换用的 `SourceAssets` 和 `Previews` 已从工程删除，不随仓库发布。`Content` 内保留了游戏实际使用的网格、骨架、动画、材质、贴图与场景；删除原始 FBX／PNG 不会删除已导入的 UE 资产。将来需要改变源网格、重新导入动画或重建资源时，必须另行取得相应导入源。

## 自备源文件后重新导入

准备本地 `SourceAssets/manifest.json`、其声明的 FBX／动画 FBX／纹理／Dynamics JSON、材质证据，以及所需环境清单与源文件。清单格式见 [MANIFEST.md](../Scripts/MANIFEST.md)。这些本地输入被 Git 忽略。

```bash
export UE_ROOT="$HOME/UE/UE_5.8"
python3 Scripts/validate_project.py --require-sources
Scripts/import_assets.sh
```

先构建 C++ Editor 模块再导入。导入会更新已有 `Content`，执行前应提交或备份自己修改的资产。重复导入会生成新的资产，应检查 Git diff，并重新运行 `Scripts/audit_content.py` 查找未再使用的旧资产。

完整导入脚本还会导入检测到的环境清单。Bianca 原场景存在缺失 HD 材质的已知边界，当前 `Content` 使用标准场景；不完整的源包不能作为完整重导输入。

`refresh_assets.py`、`refresh_catalog.py`、`refresh_environment.py` 也是编辑器工具，需要相应本地源清单。`verify_imported_assets.py` 用于导入后与源清单比对，同样需要源输入。它们不是普通构建的前置步骤。

## PC 高清源资源

本次新转换的源文件已单独保存在 [PCResources](../PCResources/README.md)，无需重建原 `SourceAssets/manifest.json`。其中 `upgrade_manifest.json` 只包含 93 张已核对当前材质／PC 参数引用链的高清纹理，目标是现有纹理资产路径；模型源采用 ActorX，尚需保留权重的 FBX 转换与骨架／材质槽适配。

普通 Python 可先检查文件，不写 Content：

```bash
python3 Scripts/validate_pc_resources.py
python3 Scripts/import_pc_textures.py --validate-files
```

在有 UE5.8 的机器上构建工程后，用编辑器执行 `Scripts/import_pc_textures.py` 默认为 `audit`；命令行模式显式增加 `-PCTextureMode=apply` 才备份并重导入。报告和备份位于 `Saved/PCAssetAnalysis`。不要用原全量导入脚本处理这份小清单；定点脚本不重建材质、其他角色或 Catalog。当前只完成源数据与文件模式验证，UE 应用、着色器、动画、外观与设备性能仍需另行验证。
