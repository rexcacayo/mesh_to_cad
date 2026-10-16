# SPDX-FileCopyrightText: 2026 Ricardo Lugaresi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Escribir un sólido de caras planas en STEP (AP214), sin librerías externas.

Cada cara de la malla (ya limpia: las caras planas son un solo polígono) sale como una
cara plana de verdad con su contorno. Fusion 360, FreeCAD, Onshape o SolidWorks lo abren
como un cuerpo sólido que se puede editar (empujar caras, hacer agujeros, redondear…).
"""
import datetime
from pathlib import Path

import numpy as np


class _Writer:
    def __init__(self):
        self.lines = []

    def add(self, text):
        self.lines.append(text)
        return f'#{len(self.lines)}'


def _f(x):
    s = f'{float(x):.6f}'.rstrip('0')
    return s + '0' if s.endswith('.') else s


def _vec(v):
    return '(' + ','.join(_f(x) for x in v) + ')'


def write(path, name, V, faces, scale=1.0):
    """V: vértices (mm), faces: listas de índices (polígonos planos, con normal hacia fuera).
    Devuelve la ruta escrita."""
    w = _Writer()
    V = np.asarray(V, float) * scale
    ctx = w.add("APPLICATION_CONTEXT('automotive design')")
    w.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,{ctx})")
    pctx = w.add(f"PRODUCT_CONTEXT('',{ctx},'mechanical')")
    prod = w.add(f"PRODUCT('{name}','{name}','',({pctx}))")
    w.add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,({prod}))")
    pdf = w.add(f"PRODUCT_DEFINITION_FORMATION('','',{prod})")
    pdc = w.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',{ctx},'design')")
    pd = w.add(f"PRODUCT_DEFINITION('design','',{pdf},{pdc})")
    pds = w.add(f"PRODUCT_DEFINITION_SHAPE('','',{pd})")
    lu = w.add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
    au = w.add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    su = w.add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    unc = w.add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-05),{lu},'distance_accuracy_value','')")
    gctx = w.add(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({unc}))"
                 f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({lu},{au},{su}))REPRESENTATION_CONTEXT('Context3D','3D'))")

    used = sorted({i for f in faces for i in f})
    vpt = {}
    for i in used:
        p = w.add(f"CARTESIAN_POINT('',{_vec(V[i])})")
        vpt[i] = w.add(f"VERTEX_POINT('',{p})")
    edges = {}

    def edge(a, b):
        key = (min(a, b), max(a, b))
        if key not in edges:
            i, j = key
            d = V[j] - V[i]
            L = float(np.linalg.norm(d))
            dd = d / L if L > 0 else np.array([1.0, 0, 0])
            p = w.add(f"CARTESIAN_POINT('',{_vec(V[i])})")
            dr = w.add(f"DIRECTION('',{_vec(dd)})")
            vc = w.add(f"VECTOR('',{dr},{_f(max(L, 1e-9))})")
            ln = w.add(f"LINE('',{p},{vc})")
            edges[key] = w.add(f"EDGE_CURVE('',{vpt[i]},{vpt[j]},{ln},.T.)")
        return edges[key], (a, b) == key

    adv = []
    for f in faces:
        P = V[f]
        # normal de Newell (robusta en polígonos de muchos lados)
        n = np.zeros(3)
        for k in range(len(f)):
            a, b = P[k], P[(k + 1) % len(f)]
            n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])])
        L = np.linalg.norm(n)
        if L < 1e-12:
            continue
        n /= L
        ref = P[1] - P[0]
        ref = ref - (ref @ n) * n
        if np.linalg.norm(ref) < 1e-12:
            ref = np.cross(n, [1, 0, 0] if abs(n[0]) < 0.9 else [0, 1, 0])
        ref /= np.linalg.norm(ref)
        oes = []
        for k in range(len(f)):
            ec, same = edge(f[k], f[(k + 1) % len(f)])
            oes.append(w.add(f"ORIENTED_EDGE('',*,*,{ec},{'.T.' if same else '.F.'})"))
        loop = w.add(f"EDGE_LOOP('',({','.join(oes)}))")
        bound = w.add(f"FACE_OUTER_BOUND('',{loop},.T.)")
        o = w.add(f"CARTESIAN_POINT('',{_vec(P[0])})")
        dz = w.add(f"DIRECTION('',{_vec(n)})")
        dx = w.add(f"DIRECTION('',{_vec(ref)})")
        ax = w.add(f"AXIS2_PLACEMENT_3D('',{o},{dz},{dx})")
        pl = w.add(f"PLANE('',{ax})")
        adv.append(w.add(f"ADVANCED_FACE('',({bound}),{pl},.T.)"))
    shell = w.add(f"CLOSED_SHELL('',({','.join(adv)}))")
    solid = w.add(f"MANIFOLD_SOLID_BREP('{name}',{shell})")
    o = w.add("CARTESIAN_POINT('',(0.,0.,0.))")
    dz = w.add("DIRECTION('',(0.,0.,1.))")
    dx = w.add("DIRECTION('',(1.,0.,0.))")
    ax = w.add(f"AXIS2_PLACEMENT_3D('',{o},{dz},{dx})")
    rep = w.add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('{name}',({ax},{solid}),{gctx})")
    w.add(f"SHAPE_DEFINITION_REPRESENTATION({pds},{rep})")

    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    head = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Mesh to CAD (Blender)'),'2;1');\n"
            f"FILE_NAME('{name}.step','{now}',('Ricardo Lugaresi Studio'),(''),'Mesh to CAD','Blender','');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n")
    body = '\n'.join(f'#{k + 1}={line};' for k, line in enumerate(w.lines))
    path = Path(path)
    path.write_text(head + body + '\nENDSEC;\nEND-ISO-10303-21;\n', encoding='ascii', errors='replace')
    return path, len(adv)
