# SPDX-FileCopyrightText: 2026 Ricardo Lugaresi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reconocer caras de CAD en una malla de triángulos (solo numpy, sin Blender).

1. Se parte la malla por las aristas vivas en «parches» lisos.
2. Cada parche se clasifica: plano, cilindro (eje, agujero) o, si mezcla cosas
   (escaneos con aristas redondeadas), se buscan planos dentro y lo que sobra se
   vuelve a probar como cilindro.
3. Lo que no encaja queda como superficie libre y no se toca.
"""
import math

import numpy as np

PRESETS = {
    # ángulo para separar caras (°), ángulo dentro de un plano (°), distancia al plano (mm), error de cilindro (mm)
    'CAD': dict(sharp=25.0, atol=1.5, dtol=0.02, ctol=0.03, nsnap=1.0),
    'SCAN': dict(sharp=35.0, atol=9.0, dtol=0.12, ctol=0.15, nsnap=4.0),
}


# --------------------------------------------------------------------------- topología
def face_data(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    a2 = np.linalg.norm(n, axis=1)
    return n / np.maximum(a2, 1e-20)[:, None], a2 * 0.5, V[F].mean(axis=1)


def face_pairs(F):
    """Pares de caras que comparten arista (solo aristas con exactamente 2 caras)."""
    e = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    e.sort(axis=1)
    face = np.tile(np.arange(len(F)), 3)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, face = e[order], face[order]
    same = (e[1:] == e[:-1]).all(axis=1)
    # descartar aristas con 3 o más caras
    ok = same.copy()
    ok[1:] &= ~same[:-1]
    ok[:-1] &= ~same[1:]
    return np.stack([face[:-1][ok], face[1:][ok]], axis=1)


def _csr(n, pairs):
    a = np.concatenate([pairs[:, 0], pairs[:, 1]])
    b = np.concatenate([pairs[:, 1], pairs[:, 0]])
    order = np.argsort(a, kind='stable')
    a, b = a[order], b[order]
    start = np.searchsorted(a, np.arange(n + 1))
    return start, b


def components(n, pairs, mask_pairs=None, members=None):
    """Componentes conexas (unión-búsqueda). `members` limita a ciertas caras."""
    parent = np.arange(n)
    if mask_pairs is not None:
        pairs = pairs[mask_pairs]
    if members is not None:
        keep = members[pairs[:, 0]] & members[pairs[:, 1]]
        pairs = pairs[keep]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    roots = np.array([find(i) for i in range(n)])
    _, lab = np.unique(roots, return_inverse=True)
    return lab


# --------------------------------------------------------------------------- ajustes
def fit_plane(P, w, N):
    """Plano de un grupo de caras: normal media ponderada por área (con pocas caras, p. ej. un
    lateral de 2 triángulos, ajustar por puntos da normales disparatadas)."""
    c = (P * w[:, None]).sum(0) / w.sum()
    n = (N * w[:, None]).sum(0)
    return n / max(np.linalg.norm(n), 1e-20), c


def fit_cylinder(P, N, w):
    """Eje = dirección a la que las normales son perpendiculares. Devuelve (eje, punto, radio,
    error medio, rms de normales fuera del plano)."""
    M = (N * w[:, None]).T @ N
    vals, vecs = np.linalg.eigh(M)
    axis = vecs[:, 0]
    off = float(np.sqrt((w * (N @ axis) ** 2).sum() / w.sum()))
    # circunferencia en el plano perpendicular al eje
    u = np.cross(axis, [1, 0, 0] if abs(axis[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    x, y = P @ u, P @ v
    A = np.stack([x, y, np.ones_like(x)], axis=1)
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A * np.sqrt(w)[:, None], b * np.sqrt(w), rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r2 = sol[2] + cx * cx + cy * cy
    if r2 <= 0:
        return None
    r = math.sqrt(r2)
    d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    err = float(np.sqrt((w * (d - r) ** 2).sum() / w.sum()))
    center = cx * u + cy * v + axis * float((P @ axis).mean())
    return axis, center, r, err, off


# --------------------------------------------------------------------------- segmentación
def _grow_planes(idx, fn, fa, fc, start, nb, atol, dtol, min_area):
    """Planos dentro de un parche por crecimiento de regiones. Devuelve lista de arrays."""
    inside = np.zeros(len(fn), bool); inside[idx] = True
    used = np.zeros(len(fn), bool)
    cos_w = math.cos(math.radians(min(atol * 2.5, 40)))   # el ruido tuerce caras sueltas; manda la distancia
    out = []
    for seed in idx[np.argsort(-fa[idx])]:
        if used[seed]:
            continue
        n, d = fn[seed].copy(), float(fn[seed] @ fc[seed])
        reg, queue, k = [seed], [seed], 0
        taken = {seed}
        sw, sn = fa[seed], fn[seed] * fa[seed]
        sp = fc[seed] * fa[seed]
        while queue:
            f = queue.pop()
            for g in nb[start[f]:start[f + 1]]:
                if g in taken or used[g] or not inside[g]:
                    continue
                if abs(n @ fc[g] - d) > dtol or fn[g] @ n < cos_w:
                    continue
                taken.add(g); reg.append(g); queue.append(g)
                sw += fa[g]; sn = sn + fn[g] * fa[g]; sp = sp + fc[g] * fa[g]
                k += 1
                if k % 64 == 0:
                    n = sn / np.linalg.norm(sn); d = float(n @ (sp / sw))
        if sw >= min_area:
            reg = np.array(reg)
            used[reg] = True
            out.append(reg)
    return out


def segment(V, F, preset='CAD', min_plane_mm2=None):
    """Devuelve (etiqueta por cara, lista de rasgos). Rasgo: dict con 'type' ('plane',
    'cylinder', 'free'), 'faces' y sus parámetros."""
    tol = PRESETS[preset]
    fn, fa, fc = face_data(V, F)
    total = float(fa.sum())
    if min_plane_mm2 is None:
        min_plane_mm2 = max(1.0, total * 0.0005)
    pairs = face_pairs(F)
    start, nb = _csr(len(F), pairs)
    smooth = (fn[pairs[:, 0]] * fn[pairs[:, 1]]).sum(1) > math.cos(math.radians(tol['sharp']))
    patch = components(len(F), pairs, smooth)
    label = np.full(len(F), -1)
    feats = []

    def add(kind, faces, **kw):
        label[faces] = len(feats)
        feats.append(dict(type=kind, faces=faces, area=float(fa[faces].sum()), **kw))

    def try_cylinder(faces):
        if fa[faces].sum() < min_plane_mm2 * 2 or len(faces) < 6:
            return False
        res = fit_cylinder(fc[faces], fn[faces], fa[faces])
        if res is None:
            return False
        axis, center, r, err, off = res
        span = np.ptp(fc[faces] @ axis)
        # que no sea casi un plano (radio enorme) ni un disparate
        if off > math.sin(math.radians(tol['atol'] + 4)) or err > max(tol['ctol'], r * 0.01) or r > 5e3:
            return False
        t = np.sort(np.arctan2(*_polar(fc[faces], axis, center)))
        gaps = np.diff(np.concatenate([t, [t[0] + 2 * math.pi]]))
        ang = math.degrees(2 * math.pi - gaps.max())        # arco que cubre (360 = cilindro entero)
        if ang < 20:                    # trozo de arco demasiado pequeño: mejor libre
            return False
        # agujero si las normales miran hacia el eje
        radial = fc[faces] - center - np.outer((fc[faces] - center) @ axis, axis)
        hole = float(((radial * fn[faces]).sum(1) * fa[faces]).sum()) < 0
        add('cylinder', faces, axis=axis, center=center, radius=r, hole=hole, length=float(span), arc=float(ang))
        return True

    for pid in range(patch.max() + 1):
        faces = np.nonzero(patch == pid)[0]
        if not len(faces):
            continue
        # ¿todo el parche es un plano?
        nrm, c = fit_plane(fc[faces], fa[faces], fn[faces])
        dist = np.abs((fc[faces] - c) @ nrm)
        ang_ok = _wquant(fn[faces] @ nrm, fa[faces], 0.05) > math.cos(math.radians(tol['atol']))
        if ang_ok and _wquant(dist, fa[faces], 0.95) <= tol['dtol'] * 2:
            if fa[faces].sum() >= min_plane_mm2 * 0.25:
                add('plane', faces, normal=nrm, point=c)
                continue
        if try_cylinder(faces):
            continue
        # mezcla: planos dentro y lo que sobra, cilindro por trozos
        planes = _grow_planes(faces, fn, fa, fc, start, nb, tol['atol'], tol['dtol'], min_plane_mm2)
        rest = np.ones(len(F), bool); rest[:] = False; rest[faces] = True
        for reg in planes:
            nrm, c = fit_plane(fc[reg], fa[reg], fn[reg])
            add('plane', reg, normal=nrm, point=c)
            rest[reg] = False
        if rest.any():
            sub = components(len(F), pairs, None, rest)
            for s in np.unique(sub[rest]):
                part = np.nonzero(rest & (sub == s))[0]
                if fa[part].sum() < min_plane_mm2 * 2 and _absorb(part, label, start, nb, feats, fa):
                    continue
                if not try_cylinder(part):
                    add('free', part)
    _merge_strips(F, pairs, label, feats, fn, fa, fc, tol)
    _merge_cylinders(pairs, label, feats, fn, fa, fc, tol)
    keep = [i for i, f in enumerate(feats) if f is not None]
    remap = -np.ones(len(feats) + 1, int)
    remap[keep] = np.arange(len(keep))
    label = np.where(label >= 0, remap[label], -1)
    return label, [feats[i] for i in keep]


def _cyl_feat(faces, fn, fa, fc, tol):
    res = fit_cylinder(fc[faces], fn[faces], fa[faces])
    if res is None:
        return None
    axis, center, r, err, off = res
    if off > math.sin(math.radians(tol['atol'] + 4)) or err > max(tol['ctol'], r * 0.01) or r > 5e3:
        return None
    t = np.sort(np.arctan2(*_polar(fc[faces], axis, center)))
    gaps = np.diff(np.concatenate([t, [t[0] + 2 * math.pi]]))
    ang = math.degrees(2 * math.pi - gaps.max())
    radial = fc[faces] - center - np.outer((fc[faces] - center) @ axis, axis)
    hole = float(((radial * fn[faces]).sum(1) * fa[faces]).sum()) < 0
    return dict(type='cylinder', faces=faces, area=float(fa[faces].sum()), axis=axis, center=center, radius=r,
                hole=hole, length=float(np.ptp(fc[faces] @ axis)), arc=float(ang))


def _merge_cylinders(pairs, label, feats, fn, fa, fc, tol):
    """Trozos del mismo agujero o eje (mismo eje y mismo radio) se juntan en uno; los planos
    pequeños pegados a un cilindro que en realidad son parte de él, también."""
    changed = True
    while changed:
        changed = False
        la, lb = label[pairs[:, 0]], label[pairs[:, 1]]
        m = (la != lb) & (la >= 0) & (lb >= 0)
        cand = {(min(a, b), max(a, b)) for a, b in zip(la[m], lb[m])}
        for a, b in sorted(cand):
            fa_, fb_ = feats[a], feats[b]
            if fa_ is None or fb_ is None:
                continue
            ta, tb = fa_['type'], fb_['type']
            if ta == 'cylinder' and tb == 'cylinder':
                if abs(fa_['axis'] @ fb_['axis']) < math.cos(math.radians(6)):
                    continue
                if abs(fa_['radius'] - fb_['radius']) > max(2 * tol['ctol'], 0.06 * fa_['radius']):
                    continue
            elif 'cylinder' in (ta, tb) and 'plane' in (ta, tb):
                pl = fa_ if ta == 'plane' else fb_
                cy = fb_ if ta == 'plane' else fa_
                if pl['area'] > cy['area'] * 0.5:
                    continue
            else:
                continue
            faces = np.concatenate([fa_['faces'], fb_['faces']])
            new = _cyl_feat(faces, fn, fa, fc, tol)
            if new is None:
                continue
            feats[a] = new; feats[b] = None
            label[faces] = a
            changed = True


def _merge_strips(F, pairs, label, feats, fn, fa, fc, tol):
    """En escaneos, un cilindro grande se puede confundir con muchas tiras planas casi
    paralelas. Los grupos de planos vecinos que se doblan poco entre sí se prueban juntos como
    cilindro; si encaja, pasan a ser un cilindro."""
    la, lb = label[pairs[:, 0]], label[pairs[:, 1]]
    m = (la != lb) & (la >= 0) & (lb >= 0)
    links = {}
    for a, b in zip(la[m], lb[m]):
        if feats[a]['type'] == 'plane' and feats[b]['type'] == 'plane':
            if feats[a]['normal'] @ feats[b]['normal'] > math.cos(math.radians(25)):
                links.setdefault(a, set()).add(b); links.setdefault(b, set()).add(a)
    seen = set()
    for s0 in list(links):
        if s0 in seen:
            continue
        group, stack = [], [s0]
        seen.add(s0)
        while stack:
            x = stack.pop(); group.append(x)
            for y in links.get(x, ()):
                if y not in seen:
                    seen.add(y); stack.append(y)
        if len(group) < 3:
            continue
        faces = np.concatenate([feats[g]['faces'] for g in group])
        res = fit_cylinder(fc[faces], fn[faces], fa[faces])
        if res is None:
            continue
        axis, center, r, err, off = res
        if off > math.sin(math.radians(tol['atol'] + 4)) or err > max(tol['ctol'], r * 0.01) or r > 5e3:
            continue
        t = np.sort(np.arctan2(*_polar(fc[faces], axis, center)))
        gaps = np.diff(np.concatenate([t, [t[0] + 2 * math.pi]]))
        ang = math.degrees(2 * math.pi - gaps.max())
        radial = fc[faces] - center - np.outer((fc[faces] - center) @ axis, axis)
        hole = float(((radial * fn[faces]).sum(1) * fa[faces]).sum()) < 0
        k = group[0]
        feats[k] = dict(type='cylinder', faces=faces, area=float(fa[faces].sum()), axis=axis, center=center,
                        radius=r, hole=hole, length=float(np.ptp(fc[faces] @ axis)), arc=float(ang))
        label[faces] = k
        for g in group[1:]:
            feats[g] = None


def _wquant(x, w, q):
    o = np.argsort(x)
    cw = np.cumsum(w[o]); cw /= cw[-1]
    return float(x[o][min(np.searchsorted(cw, q), len(x) - 1)])


def _absorb(part, label, start, nb, feats, fa):
    """Trocito sin forma propia (ruido de escaneo entre dos caras): se lo queda el rasgo vecino
    que más borde comparte con él."""
    votes = {}
    for f in part:
        for g in nb[start[f]:start[f + 1]]:
            if label[g] >= 0:
                votes[label[g]] = votes.get(label[g], 0) + 1
    if not votes:
        return False
    best = max(votes, key=votes.get)
    label[part] = best
    feats[best]['faces'] = np.concatenate([feats[best]['faces'], part])
    feats[best]['area'] += float(fa[part].sum())
    return True


def _polar(P, axis, center):
    u = np.cross(axis, [1, 0, 0] if abs(axis[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    Q = P - center
    return Q @ v, Q @ u


# --------------------------------------------------------------------------- medidas limpias
def _nice(d):
    """Diámetro «de verdad»: entero o medio milímetro si está cerca; si no, décima."""
    for step in (1.0, 0.5):
        q = round(d / step) * step
        if abs(q - d) <= max(0.06, d * 0.01):
            return q
    return round(d, 1)


def tidy(feats, preset='CAD'):
    """Endereza normales y ejes a un mismo sistema (piezas mecánicas: caras a 90°) y redondea
    los diámetros. Devuelve la base (3×3) del sistema encontrado."""
    tol = math.radians(PRESETS[preset]['nsnap'])
    planes = sorted([f for f in feats if f['type'] == 'plane'], key=lambda f: -f['area'])
    dirs = [f['normal'] for f in planes] + [f['axis'] for f in feats if f['type'] == 'cylinder']
    if not dirs:
        return np.eye(3)
    e1 = dirs[0] / np.linalg.norm(dirs[0])
    e2 = None
    for d in dirs[1:]:
        if abs(d @ e1) < math.sin(tol):
            e2 = d - (d @ e1) * e1
            e2 /= np.linalg.norm(e2)
            break
    if e2 is None:
        e2 = np.cross(e1, [1, 0, 0] if abs(e1[0]) < 0.9 else [0, 1, 0]); e2 /= np.linalg.norm(e2)
    e3 = np.cross(e1, e2)
    B = np.stack([e1, e2, e3])

    def snap(d):
        c = B @ d
        k = int(np.argmax(np.abs(c)))
        if abs(c[k]) > math.cos(tol):
            return np.sign(c[k]) * B[k]
        return d
    for f in feats:
        if f['type'] == 'plane':
            f['normal'] = snap(f['normal'])
        elif f['type'] == 'cylinder':
            f['axis'] = snap(f['axis'])
            f['diameter'] = _nice(2 * f['radius'])
            f['radius'] = f['diameter'] / 2
    return B


# --------------------------------------------------------------------------- encajar vértices
def snap_vertices(V, F, label, feats, rounds=12):
    """Lleva cada vértice a las superficies de sus caras (plano exacto, cilindro exacto); los
    vértices de una arista o esquina quedan en la intersección. Lo libre no se mueve."""
    n = len(V)
    # superficies por vértice
    vf = [[] for _ in range(n)]
    for k in range(3):
        for fi, vi in enumerate(F[:, k]):
            lab = label[fi]
            if lab >= 0 and feats[lab]['type'] != 'free' and lab not in vf[vi]:
                vf[vi].append(lab)
    free_v = np.zeros(n, bool)
    for fi in np.nonzero([label[i] < 0 or feats[label[i]]['type'] == 'free' for i in range(len(F))])[0]:
        free_v[F[fi]] = True
    P = V.copy()
    todo = [i for i in range(n) if vf[i] and not free_v[i]]
    for _ in range(rounds):
        moved = 0.0
        for i in todo:
            acc = np.zeros(3)
            for lab in vf[i]:
                acc += _project(P[i], feats[lab])
            q = acc / len(vf[i])
            moved = max(moved, float(np.linalg.norm(q - P[i])))
            P[i] = q
        if moved < 1e-5:
            break
    return P


def _project(p, f):
    if f['type'] == 'plane':
        nrm, c = f['normal'], f['point']
        return p - ((p - c) @ nrm) * nrm
    axis, c, r = f['axis'], f['center'], f['radius']
    q = p - c
    along = (q @ axis) * axis
    radial = q - along
    L = np.linalg.norm(radial)
    if L < 1e-12:
        return p
    return c + along + radial * (r / L)


def summary(feats):
    planes = [f for f in feats if f['type'] == 'plane']
    cyls = [f for f in feats if f['type'] == 'cylinder']
    free = sum(f['area'] for f in feats if f['type'] == 'free')
    total = sum(f['area'] for f in feats) or 1.0
    # agujeros y ejes de verdad (vuelta casi entera); los arcos cortos son redondeos de arista
    full = [f for f in cyls if f.get('arc', 0) >= 180]
    holes = sorted((f.get('diameter', round(2 * f['radius'], 1)) for f in full if f['hole']), reverse=True)
    shafts = sorted((f.get('diameter', round(2 * f['radius'], 1)) for f in full if not f['hole']), reverse=True)
    return dict(planes=len(planes), cylinders=len(cyls), rounds=len(cyls) - len(full), holes=holes, shafts=shafts,
                free_share=free / total)
