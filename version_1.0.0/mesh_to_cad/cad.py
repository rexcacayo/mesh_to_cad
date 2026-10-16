# SPDX-FileCopyrightText: 2026 Ricardo Lugaresi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mesh to CAD dentro de Blender: de la malla al sólido limpio y al STEP."""
import json
import math
import time
from pathlib import Path

import bmesh
import bpy
import numpy as np

from . import common, fit, step
from . import meshops as mo

COLORS = {'plane': ('CAD plano', (0.62, 0.68, 0.76, 1)), 'cylinder': ('CAD cilindro', (0.95, 0.55, 0.15, 1)),
          'hole': ('CAD agujero', (0.20, 0.72, 0.35, 1)), 'free': ('CAD libre', (0.85, 0.20, 0.25, 1))}
MAX_FACES = 250_000


def _read(context, obj, mm):
    graph = context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(graph)
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
        n = len(me.loop_triangles)
        tri = np.empty(n * 3, np.int64); me.loop_triangles.foreach_get('vertices', tri)
        co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get('co', co)
    finally:
        ev.to_mesh_clear()
    M = np.array(obj.matrix_world)
    V = (co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]) * mm
    F = tri.reshape(-1, 3)
    if np.linalg.det(M[:3, :3]) < 0:
        F = F[:, ::-1]
    return V, F


def _lighter(context, obj, max_faces):
    """Copia temporal con menos caras (los escaneos traen millones)."""
    tmp = obj.copy(); tmp.data = obj.data.copy()
    context.scene.collection.objects.link(tmp)
    mod = tmp.modifiers.new('cad_reducir', 'DECIMATE')
    mod.ratio = max_faces / max(1, len(obj.data.polygons))
    return tmp


def guess_preset(V, F):
    """STL salido de un programa de CAD: muchas caras vecinas exactamente en el mismo plano.
    Escaneo o IA: la superficie tiembla un poco en todas partes."""
    fn, _fa, _fc = fit.face_data(V, F)
    pairs = fit.face_pairs(F)
    if not len(pairs):
        return 'CAD'
    dots = (fn[pairs[:, 0]] * fn[pairs[:, 1]]).sum(1)
    exact = float((dots > math.cos(math.radians(0.05))).mean())
    return 'CAD' if exact > 0.25 else 'SCAN'


def _material(key):
    name, rgba = COLORS[key]
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = rgba
    return mat


def convert(context, obj, preset='AUTO', straighten=True, round_dims=True, colorize=True, unit='AUTO'):
    t0 = time.time()
    code, mm = common.resolve_unit(context.scene, unit, obj)
    notes = []
    src = obj
    tmp = None
    if len(obj.data.polygons) > MAX_FACES:
        tmp = _lighter(context, obj, MAX_FACES)
        src = tmp
        notes.append(f'malla aligerada a {MAX_FACES // 1000} mil caras para analizarla')
    try:
        V, F = _read(context, src, mm)
    finally:
        if tmp is not None:
            common.remove_objects([tmp])
    V, F, _keep = mo.weld(V, F)
    if not len(F):
        raise common.AddonError('La malla no tiene caras útiles.')
    if preset == 'AUTO':
        preset = guess_preset(V, F)
    notes.append('origen: STL de CAD' if preset == 'CAD' else 'origen: escaneo o IA (tolerancias más anchas)')
    label, feats = fit.segment(V, F, preset)
    B = fit.tidy(feats, preset) if round_dims else np.eye(3)
    fn0, _a, _c = fit.face_data(V, F)
    P = fit.snap_vertices(V, F, label, feats)
    # donde encajar dé la vuelta a algún triángulo (aristas redondeadas de un escaneo), esos
    # vértices vuelven a su sitio: mejor un poco de ruido que una malla que se cruza
    restored = 0
    for _ in range(4):
        fn1, _fa1, _c = fit.face_data(P, F)
        bad = (fn0 * fn1).sum(1) < 0.2
        if not bad.any():
            break
        vs = np.unique(F[bad])
        restored += len(vs)
        P[vs] = V[vs]
    fn1, _fa1, _c = fit.face_data(P, F)
    flipped = int(((fn0 * fn1).sum(1) < 0).sum())
    if straighten:
        R = np.stack([B[2], B[1], -B[0]])          # la cara plana más grande, apoyada abajo
        if np.linalg.det(R) < 0:
            R[0] = -R[0]
        P = P @ R.T
        lo, hi = P.min(0), P.max(0)
        P = P - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])

    # malla nueva: una cara de polígono por cada plano (se disuelven los triángulos del mismo plano)
    kind = np.array([feats[l]['type'] if l >= 0 else 'free' for l in label])
    hole = np.array([bool(feats[l].get('hole')) if l >= 0 else False for l in label])
    key = np.where(kind == 'cylinder', np.where(hole, 'hole', 'cylinder'), kind)
    order = ['plane', 'cylinder', 'hole', 'free']
    mat_index = np.array([order.index(k) for k in key], np.int32)
    me = bpy.data.meshes.new(f'{obj.name}_cad')
    me.vertices.add(len(P)); me.vertices.foreach_set('co', (P / mm).astype(np.float32).ravel())
    me.loops.add(len(F) * 3); me.loops.foreach_set('vertex_index', F.astype(np.int32).ravel())
    me.polygons.add(len(F)); me.polygons.foreach_set('loop_start', np.arange(0, len(F) * 3, 3, dtype=np.int32))
    for k in order:
        me.materials.append(_material(k))
    me.polygons.foreach_set('material_index', mat_index)
    # cada rasgo, su propia «isla» para no fundir dos planos distintos que sean coplanarios
    me.update(calc_edges=True)
    bm = bmesh.new(); bm.from_mesh(me)
    try:
        lay = bm.faces.layers.int.new('cad_rasgo')
        for f, lab in zip(bm.faces, label):
            f[lay] = int(lab)
        # aristas que separan rasgos distintos: no se disuelven
        for e in bm.edges:
            if len(e.link_faces) == 2 and e.link_faces[0][lay] != e.link_faces[1][lay]:
                e.seam = True
        planar = [f for f in bm.faces if f.material_index == 0]
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(0.2), use_dissolve_boundaries=False,
                                 verts=list({v for f in planar for v in f.verts}),
                                 edges=list({e for f in planar for e in f.edges if not e.seam}),
                                 delimit={'SEAM', 'MATERIAL'})
        bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
        bm.to_mesh(me)
    finally:
        bm.free()
    for e in me.edges:
        e.use_seam = False
    me.update()
    out = bpy.data.objects.new(f'{obj.name}_cad', me)
    col = obj.users_collection[0] if obj.users_collection else context.scene.collection
    col.objects.link(out)
    if not colorize:
        while len(me.materials) > 1:
            me.materials.pop()
    out[common.UNIT_PROP] = code
    out['taller_origen'] = obj.name
    sm = fit.summary(feats)
    sm['faces_before'] = int(len(F)); sm['faces_after'] = int(len(me.polygons)); sm['preset'] = preset
    out['cad_resumen'] = json.dumps(sm)
    obj.hide_set(True); obj.hide_render = True
    for o in context.selected_objects:
        o.select_set(False)
    out.select_set(True)
    context.view_layer.objects.active = out
    if flipped > max(3, len(F) * 0.002):
        notes.append(f'ojo: {flipped} triángulos se han dado la vuelta al encajar: revisa esa zona')
    notes.append(f'{len(F):,} triángulos → {len(me.polygons):,} caras'.replace(',', '.'))
    return out, sm, notes, time.time() - t0


def export_step(context, objs, folder):
    folder = common.export_folder(folder)
    dest = Path(bpy.path.abspath(folder))
    dest.mkdir(parents=True, exist_ok=True)
    files = []
    graph = context.evaluated_depsgraph_get()
    for obj in objs:
        code, mm = common.resolve_unit(context.scene, 'AUTO', obj)
        ev = obj.evaluated_get(graph)
        me = ev.to_mesh()
        try:
            M = np.array(obj.matrix_world)
            co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get('co', co)
            V = (co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]) * mm
            faces = [list(p.vertices) for p in me.polygons]
            if np.linalg.det(M[:3, :3]) < 0:
                faces = [f[::-1] for f in faces]
            ecount = {}
            for f in faces:
                for k in range(len(f)):
                    e = (min(f[k], f[(k + 1) % len(f)]), max(f[k], f[(k + 1) % len(f)]))
                    ecount[e] = ecount.get(e, 0) + 1
            closed = all(c == 2 for c in ecount.values())
        finally:
            ev.to_mesh_clear()
        name = common.safe_name(obj.name)
        used = {p.stem.casefold() for p in dest.iterdir()}
        target = dest / (common.unique_name(name, used) + '.step')
        path, nf = step.write(target, name, V, faces)
        files.append((path, nf, closed))
    return files
