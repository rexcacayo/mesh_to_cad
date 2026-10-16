# SPDX-FileCopyrightText: 2026 Ricardo Lugaresi
# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {'name': 'Mesh to CAD', 'author': 'Ricardo Lugaresi', 'version': (1, 0, 0),
           'blender': (4, 2, 0), 'location': 'Vista 3D > N > Mesh to CAD',
           'description': 'Convierte un STL o un escaneo en un sólido limpio de caras planas y cilindros, con las medidas '
                          'de agujeros y ejes, y lo guarda en STEP para editarlo en Fusion, FreeCAD u Onshape',
           'category': 'Object'}

import json
from pathlib import Path

import bpy

from . import cad, common


class CadProps(bpy.types.PropertyGroup):
    model_unit: bpy.props.EnumProperty(name='Unidades', items=common.UNIT_ITEMS, default='AUTO')
    preset: bpy.props.EnumProperty(name='Origen', items=[
        ('AUTO', 'Auto', 'Lo adivina por la malla'),
        ('CAD', 'STL de CAD', 'Exportado de Fusion, FreeCAD, Tinkercad…: caras perfectas'),
        ('SCAN', 'Escaneo / IA', 'Superficie con ruido: tolerancias más anchas')], default='AUTO')
    straighten: bpy.props.BoolProperty(name='Enderezar a los ejes', default=True,
                                       description='Gira la pieza para que sus caras queden a escuadra con X, Y, Z y la apoya en la cama')
    round_dims: bpy.props.BoolProperty(name='Medidas limpias', default=True,
                                       description='Caras a 90° exactos y diámetros redondeados (7,96 → 8)')
    colorize: bpy.props.BoolProperty(name='Colorear lo reconocido', default=True,
                                     description='Gris: plano · naranja: eje · verde: agujero · rojo: superficie libre')
    export_folder: bpy.props.StringProperty(name='Carpeta', default='//CAD', subtype='DIR_PATH')
    show_fine: bpy.props.BoolProperty(name='Ajustes', default=False)
    report: bpy.props.StringProperty(default='')


def _is_model(obj):
    return obj is not None and obj.type == 'MESH' and not common.is_helper(obj) and not obj.get('showcase') and obj.visible_get()


def _model(context):
    for obj in [context.active_object] + list(context.selected_objects):
        if _is_model(obj):
            return obj
    return None


def _dlist(values):
    out = {}
    for d in values:
        out[d] = out.get(d, 0) + 1
    return ', '.join((f'Ø{d:g}' + (f' ×{n}' if n > 1 else '')) for d, n in out.items())


class OBJECT_OT_cad_convert(bpy.types.Operator):
    """Reconoce caras planas, ejes y agujeros y deja un sólido limpio; el original queda oculto"""
    bl_idname = 'object.cad_convert'; bl_label = 'Convertir a CAD'; bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context)

    def execute(self, context):
        p = context.scene.cad_props
        obj = _model(context)
        if obj is None:
            self.report({'ERROR'}, 'Selecciona la pieza (una malla).'); return {'CANCELLED'}
        try:
            out, sm, notes, secs = cad.convert(context, obj, p.preset, p.straighten, p.round_dims, p.colorize, p.model_unit)
        except common.AddonError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        p.report = '\n'.join(notes)
        self.report({'INFO'}, f'{sm["planes"]} caras planas, {sm["cylinders"]} cilindros ({secs:.0f} s)')
        return {'FINISHED'}


class OBJECT_OT_cad_step(bpy.types.Operator):
    """Guarda las piezas seleccionadas en STEP (sólido editable en Fusion, FreeCAD, Onshape…)"""
    bl_idname = 'object.cad_step'; bl_label = 'Guardar STEP'; bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context)

    def execute(self, context):
        p = context.scene.cad_props
        objs = [o for o in context.selected_objects if _is_model(o)]
        if not objs:
            self.report({'ERROR'}, 'Selecciona la pieza convertida.'); return {'CANCELLED'}
        try:
            files = cad.export_step(context, objs, p.export_folder)
        except (common.AddonError, OSError) as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        open_ = [f for f in files if not f[2]]
        msg = f'{len(files)} STEP en {Path(files[0][0]).parent}'
        if open_:
            msg += ' · ojo: alguna pieza no está cerrada (pásala antes por Print Ready)'
        self.report({'WARNING' if open_ else 'INFO'}, msg)
        return {'FINISHED'}


def _wrap(layout, text, width=40):
    line = ''
    for w in text.split():
        if len(line) + len(w) + 1 > width and line:
            layout.label(text=line); line = w
        else:
            line = f'{line} {w}'.strip()
    if line:
        layout.label(text=line)


class VIEW3D_PT_cad(bpy.types.Panel):
    bl_label = 'Mesh to CAD'; bl_idname = 'VIEW3D_PT_cad'; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'
    bl_category = 'Mesh to CAD'

    def draw(self, context):
        layout = self.layout; p = context.scene.cad_props
        if common.draw_mode_warning(layout, context):
            return
        model = _model(context)
        box = layout.box()
        box.label(text=model.name if model else 'Selecciona la pieza', icon='OBJECT_DATA' if model else 'INFO')
        col = box.column(); col.scale_y = 1.8
        col.operator('object.cad_convert', icon='MOD_EDGESPLIT')

        info = model.get('cad_resumen') if model else None
        if info:
            sm = json.loads(info)
            box = layout.box()
            box.label(text=f'{sm["planes"]} caras planas · {sm["cylinders"]} cilindros', icon='CHECKMARK')
            if sm['holes']:
                _wrap(box, 'Agujeros: ' + _dlist(sm['holes']))
            if sm['shafts']:
                _wrap(box, 'Ejes: ' + _dlist(sm['shafts']))
            r = box.row(); r.alert = sm['free_share'] > 0.3
            r.label(text=f'Superficie libre: {sm["free_share"] * 100:.0f} %', icon='INFO')
            col = box.column(); col.scale_y = 1.3
            col.operator('object.cad_step', icon='EXPORT')
        if p.report:
            col = layout.column(align=True); col.scale_y = .8
            for line in p.report.split('\n'):
                _wrap(col, '· ' + line)

        box = layout.box()
        box.prop(p, 'show_fine', icon='TRIA_DOWN' if p.show_fine else 'TRIA_RIGHT', emboss=False)
        if p.show_fine:
            box.prop(p, 'preset')
            box.prop(p, 'straighten')
            box.prop(p, 'round_dims')
            box.prop(p, 'colorize')
            box.prop(p, 'model_unit')
            box.prop(p, 'export_folder')


classes = (CadProps, OBJECT_OT_cad_convert, OBJECT_OT_cad_step, VIEW3D_PT_cad)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.cad_props = bpy.props.PointerProperty(type=CadProps)


def unregister():
    del bpy.types.Scene.cad_props
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
