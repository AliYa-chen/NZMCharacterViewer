# SourceAssets 清单

文件路径相对于 `SourceAssets`，必须存在且不能越出该目录。新增角色必须有实际网格；校验器拒绝空服装、空部件或重复 id。

```json
{
  "schema_version": 1,
  "characters": [{
    "id": "naimei",
    "name": "Naimei",
    "outfits": [{
      "id": "suit_a",
      "name": "Suit A",
      "parts": [{
        "id": "body",
        "name": "身体",
        "mesh": "Naimei/Suit_A/FBX/SK_Char_Naimei_Suit_A_UI_Body_Mesh.fbx",
        "default_visible": true,
        "available_in_viewer": true,
        "transform": {"translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "materials": []
      }]
    }]
  }],
  "limitations": []
}
```

此片段展示层级。`SourceAssets` 不随仓库提供；需要重新导入时，自备完整的 `SourceAssets/manifest.json` 与其中声明的源文件，见 [导入说明](../Docs/ASSET_IMPORT.md)。普通克隆和编译使用已提交的 `Content`。

`id` 使用英文字母、数字、下划线；`name` 是界面显示名称。部件变换采用 UE 厘米、Z 向上坐标，旋转依次为 pitch／yaw／roll；实际提供的 FBX 已包含单位／轴信息，默认使用单位变换。

`available_in_viewer:false` 的部件仍参与源文件完整性校验，但不导入运行时 DataAsset、不显示开关。只有已完成挂接、代理几何与动画同步验证的部件才应开放。`default_visible:false` 只适用于已可用、初始关闭的部件，不能替代可用性验证。

每个 `materials` 项包含：

| 字段 | 用途 |
| --- | --- |
| `slot` | 实际导出 FBX／GLB 的槽序号；不是原 cooked 网格里未经映射的槽序号 |
| `slot_name` | 实际导出槽名，优先用于 UE 导入后的匹配 |
| `name` | 来源材质名 |
| `base_color`／`normal`／`emissive` | 已确认的 PNG 路径，可为 null |
| `roughness`／`metallic`／`opacity` | 单独确认的 PNG 路径，可为 null；读取 R 通道 |
| `base_color_factor` | 线性 RGB 与 alpha，默认 `[1,1,1,1]`；有贴图时 RGB 相乘 |
| `roughness_factor`／`metallic_factor`／`specular_factor` | 材质标量；没有可证实的源值时，`material_policy.py` 采用按类别记录的 stock UE 近似 |
| `blend_mode` | `opaque`、`masked` 或 `translucent` |
| `opacity_factor` | 无独立透明度贴图时的透明度常量；head_AO 的明确降级为 0 |
| `two_sided` | 是否双面 |
| `mapping_verified`／`source_parameters`／`notes` | 证据和限制，写入导入报告 |

原始 packed 图仍可作为证据保存，但不能把未拆通道的 RAM／PSAR 图直接假定为 roughness。

可选 `part.animation` 是动画专用 FBX 路径；导入时绑定该部件的 Skeleton，失败或类型不匹配即停止。`animation_loop` 默认为 true。无动画不影响静态查看。

多角色、多服装继续使用同一层级，不需要修改 C++。`outfit.available_in_viewer:false` 的完整源记录不加入运行时；没有任何可用服装的角色同样不显示。尚未转换且没有文件的记录应放在独立资源库存报告中，不能用空清单充当可用服装。校验器仍核对清单所有源文件，避免无效路径被误当成功。

生成的 ViewerCatalog 使用软资产引用，运行时按服装异步加载并释放旧引用。源纹理导入按路径和用途去重；几何八权重上限明确校验，超过上限时停止，不能静默丢权重。

脚本仅支持这一查看器声明的字段；未声明的源证据不会自动变成材质连接。引擎导入、FBX 槽顺序、骨骼权重和移动端画面仍需在 UE 与设备中验证。
