"""Stock UE approximation of the source's separate iris/sclera UV layers.

Original MF_Basic_Eye_Layer samples IrisMap through MF_Data_Eye and ScleraMap
through ScaleUVsByCenter. The cooked UV arithmetic/output nodes are absent.
This explicit radial remap uses serialized irisUVRadius and ScleraScale as
constraints; it does not reproduce corneal refraction, fake light or the BRDF.
"""
import unreal


def build_eye(material, spec, import_texture, expression):
    data = spec.get('eye_reconstruction')
    if not data:
        return None
    edit = unreal.MaterialEditingLibrary
    def node(cls, x, y):
        return expression(material, cls, x, y)
    def scalar(value, x, y):
        obj = node(unreal.MaterialExpressionConstant, x, y)
        obj.set_editor_property('r', float(value))
        return obj
    def color(value, x, y):
        obj = node(unreal.MaterialExpressionConstant3Vector, x, y)
        obj.set_editor_property('constant', unreal.LinearColor(*value, 1.))
        return obj
    def wire(a, output, b, pin):
        # Shader property names and editor display pin names differ: the
        # TextureSample Coordinates member is exposed as UVs in UE 5.8.
        names = [str(name) for name in edit.get_material_expression_input_names(b)]
        aliases = {'Coordinates': 'UVs'}
        target = aliases.get(pin, pin)
        if target not in names and len(names) == 1:
            target = names[0]
        if not edit.connect_material_expressions(a, output, b, target):
            raise RuntimeError('Eye material expression connection failed: '
                               + b.get_class().get_name() + ' ' + target + ' available=' + str(names))
    def binary(cls, a, b, x, y):
        obj = node(cls, x, y)
        wire(a, '', obj, 'A'); wire(b, '', obj, 'B')
        return obj
    def sample(relative, uv, x, y):
        obj = node(unreal.MaterialExpressionTextureSample, x, y)
        obj.set_editor_property('texture', import_texture(relative, 'base_color'))
        obj.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
        wire(uv, '', obj, 'Coordinates')
        return obj
    uv = node(unreal.MaterialExpressionTextureCoordinate, -1800, 1000)
    uv.set_editor_property('coordinate_index', 0)
    center = node(unreal.MaterialExpressionConstant2Vector, -1800, 1150)
    center.set_editor_property('r', .5); center.set_editor_property('g', .5)
    centered = binary(unreal.MaterialExpressionSubtract, uv, center, -1600, 1000)
    diameter = scalar(2. * data['iris_radius'], -1600, 1200)
    iris_uv = binary(unreal.MaterialExpressionDivide, centered, diameter, -1400, 1000)
    iris_uv = binary(unreal.MaterialExpressionAdd, iris_uv, center, -1200, 1000)
    sclera_scale = scalar(data['sclera_scale'], -1600, 1400)
    sclera_uv = binary(unreal.MaterialExpressionMultiply, centered, sclera_scale, -1400, 1400)
    sclera_uv = binary(unreal.MaterialExpressionAdd, sclera_uv, center, -1200, 1400)
    iris = sample(data['iris'], iris_uv, -1000, 1000)
    sclera = sample(data['sclera'], sclera_uv, -1000, 1400)
    iris_tint = color(data['iris_color'], -1000, 1180)
    sclera_tint = color(data['sclera_color'], -1000, 1580)
    iris_rgb = node(unreal.MaterialExpressionMultiply, -750, 1000)
    wire(iris, 'RGB', iris_rgb, 'A'); wire(iris_tint, '', iris_rgb, 'B')
    sclera_rgb = node(unreal.MaterialExpressionMultiply, -750, 1400)
    wire(sclera, 'RGB', sclera_rgb, 'A'); wire(sclera_tint, '', sclera_rgb, 'B')
    distance = binary(unreal.MaterialExpressionDistance, uv, center, -1400, 1750)
    radius = scalar(data['iris_radius'], -1400, 1900)
    offset = binary(unreal.MaterialExpressionSubtract, distance, radius, -1200, 1750)
    feather = scalar(data['iris_edge_width'], -1200, 1950)
    alpha = binary(unreal.MaterialExpressionDivide, offset, feather, -1000, 1750)
    saturate = node(unreal.MaterialExpressionSaturate, -800, 1750); wire(alpha, '', saturate, 'Input')
    mask = node(unreal.MaterialExpressionOneMinus, -600, 1750); wire(saturate, '', mask, 'Input')
    blend = node(unreal.MaterialExpressionLinearInterpolate, -400, 1250)
    wire(sclera_rgb, '', blend, 'A'); wire(iris_rgb, '', blend, 'B'); wire(mask, '', blend, 'Alpha')
    edit.connect_material_property(blend, '', unreal.MaterialProperty.MP_BASE_COLOR)
    if data.get('emissive'):
        emissive = sample(data['emissive'], iris_uv, -1000, 2100)
        intensity = scalar(data['emissive_intensity'], -1000, 2300)
        multiplied = node(unreal.MaterialExpressionMultiply, -750, 2100)
        wire(emissive, 'RGB', multiplied, 'A'); wire(intensity, '', multiplied, 'B')
        clipped = binary(unreal.MaterialExpressionMultiply, multiplied, mask, -500, 2100)
        edit.connect_material_property(clipped, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return data
