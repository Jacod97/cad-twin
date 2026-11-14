"""USD 장면 → pyrender(EGL, GPU 오프스크린) 렌더러. GUI 없이 PNG 생성.

- instanceable 프림은 프로토타입을 한 번만 읽고 인스턴스 pose로 그림 (182개 직선 컨베이어 = 1개 메시)
- 재질: UsdPreviewSurface diffuseColor (텍스처면 이미지, 상수면 색), 없으면 displayColor
- Cube / Sphere / Mesh gprim 지원
"""
import os

os.environ.setdefault("PYOPENGL_PLATFORM", "egl")

from pathlib import Path

import numpy as np
import pyrender
import trimesh
from PIL import Image
from pxr import Gf, Usd, UsdGeom, UsdShade

TEX_MAX = 1024
_TEX_CACHE = {}


# ---------------------------------------------------------------- 재질
def _texture(path):
    if path not in _TEX_CACHE:
        im = Image.open(path).convert("RGB")
        if max(im.size) > TEX_MAX:
            im.thumbnail((TEX_MAX, TEX_MAX))
        _TEX_CACHE[path] = pyrender.Texture(source=np.asarray(im), source_channels="RGB",
                                            sampler=pyrender.Sampler(wrapS=10497, wrapT=10497))
    return _TEX_CACHE[path]


def _material(prim, display_color):
    mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    color, tex = None, None
    if mat:
        for ch in Usd.PrimRange(mat.GetPrim()):
            sh = UsdShade.Shader(ch)
            if not sh or sh.GetIdAttr().Get() != "UsdPreviewSurface":
                continue
            inp = sh.GetInput("diffuseColor")
            if inp:
                src = inp.GetConnectedSources()[0]
                if src:
                    tsh = UsdShade.Shader(src[0].source.GetPrim())
                    f = tsh.GetInput("file")
                    if f and f.Get():
                        p = f.Get().resolvedPath
                        if p and Path(p).exists():
                            tex = p
                elif inp.Get() is not None:
                    color = list(inp.Get())
            break
    if color is None:
        color = list(display_color) if display_color is not None else [0.7, 0.7, 0.7]
    kw = dict(baseColorFactor=[float(v) for v in color] + [1.0], metallicFactor=0.15, roughnessFactor=0.65, doubleSided=True)
    if tex:
        kw["baseColorTexture"] = _texture(tex)
        kw["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]   # 정수면 pyrender가 uint8로 보고 /255 함
    return pyrender.MetallicRoughnessMaterial(**kw), (tex, tuple(np.round(color, 3)))


# ---------------------------------------------------------------- 메시 변환
def _mesh_arrays(prim, M):
    """USD Mesh → (positions, normals, uvs) per-corner 삼각형 배열. M: 프림→기준 좌표 4x4 (row-vector)."""
    m = UsdGeom.Mesh(prim)
    pts = np.array(m.GetPointsAttr().Get(), np.float64)
    counts = np.array(m.GetFaceVertexCountsAttr().Get())
    idx = np.array(m.GetFaceVertexIndicesAttr().Get())
    if len(pts) == 0 or len(counts) == 0:
        return None
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    tri = []
    for k in range(1, counts.max() - 1):
        sel = counts > k + 1
        s = starts[sel]
        tri.append(np.stack([s, s + k, s + k + 1], 1))
    tri = np.concatenate(tri)  # 코너 인덱스
    if m.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
        tri = tri[:, ::-1]
    corners = tri.reshape(-1)
    P = pts[idx[corners]]
    P = (np.c_[P, np.ones(len(P))] @ M)[:, :3]

    def per_corner(values, interp, indices=None):
        if values is None:
            return None
        v = np.array(values, np.float64)
        if indices is not None and len(indices):
            v = v[np.array(indices)]
        if interp == "faceVarying":
            return v[corners]
        if interp in ("vertex", "varying"):
            return v[idx[corners]]
        if interp == "uniform":
            face_of_corner = np.repeat(np.arange(len(counts)), counts)
            return v[face_of_corner[corners]]
        return None

    N = per_corner(m.GetNormalsAttr().Get(), m.GetNormalsInterpolation())
    if N is not None and len(N) == len(P):
        R = M[:3, :3]
        N = N @ np.linalg.inv(R).T  # 법선 변환 (row-vector)
        N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-12
        if m.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
            N = -N
    else:
        N = None
    UV = None
    pv = UsdGeom.PrimvarsAPI(prim)
    for name in ("st", "st0", "UVMap"):
        if pv.HasPrimvar(name):
            p = pv.GetPrimvar(name)
            UV = per_corner(p.Get(), p.GetInterpolation(), p.GetIndices() if p.IsIndexed() else None)
            if UV is not None:
                UV = np.c_[UV[:, 0], 1.0 - UV[:, 1]]
            break
    return P, N, UV


def _np(M):
    return np.array(M, np.float64)


def _display_color(prim):
    g = UsdGeom.Gprim(prim)
    c = g.GetDisplayColorAttr().Get() if g else None
    return list(c[0]) if c else None


def _collect(root, xc, base_inv=None, proxies=False):
    """root 아래 gprim들을 기준좌표 메시로 모음 → [(재질키, material, P, N, UV)]"""
    out = []
    pred = Usd.TraverseInstanceProxies() if proxies else Usd.PrimDefaultPredicate
    for q in Usd.PrimRange(root, pred):
        if q.IsInstance() and not proxies:
            continue
        img = UsdGeom.Imageable(q)
        if img and img.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        M = _np(xc.GetLocalToWorldTransform(q))
        if base_inv is not None:
            M = M @ base_inv
        if q.IsA(UsdGeom.Mesh):
            arr = _mesh_arrays(q, M)
            if arr is None:
                continue
            mat, key = _material(q, _display_color(q))
            out.append((key, mat) + arr)
        elif q.IsA(UsdGeom.Cube) or q.IsA(UsdGeom.Sphere):
            if q.IsA(UsdGeom.Cube):
                s = UsdGeom.Cube(q).GetSizeAttr().Get()
                tm = trimesh.creation.box((s, s, s))
            else:
                r = UsdGeom.Sphere(q).GetRadiusAttr().Get()
                tm = trimesh.creation.icosphere(subdivisions=2, radius=r)
            P = tm.vertices[tm.faces.reshape(-1)]
            P = (np.c_[P, np.ones(len(P))] @ M)[:, :3]
            fn = np.repeat(tm.face_normals, 3, axis=0) @ np.linalg.inv(M[:3, :3]).T
            fn /= np.linalg.norm(fn, axis=1, keepdims=True) + 1e-12
            if q.IsA(UsdGeom.Sphere):
                fn = tm.vertex_normals[tm.faces.reshape(-1)]
            mat, key = _material(q, _display_color(q))
            out.append((key, mat, P, fn, None))
    return out


def _merge(parts, poses=None):
    """같은 재질키끼리 합쳐 pyrender.Mesh 목록으로."""
    groups = {}
    for key, mat, P, N, UV in parts:
        g = groups.setdefault(key, dict(mat=mat, P=[], N=[], UV=[], has_uv=key[0] is not None))
        g["P"].append(P)
        g["N"].append(N if N is not None else np.zeros_like(P))
        g["UV"].append(UV if UV is not None else np.zeros((len(P), 2)))
    prims = []
    for key, g in groups.items():
        P = np.concatenate(g["P"]).astype(np.float32)
        N = np.concatenate(g["N"]).astype(np.float32)
        if not np.any(N):
            N = None
        UV = np.concatenate(g["UV"]).astype(np.float32) if g["has_uv"] else None
        idx = np.arange(len(P)).reshape(-1, 3)
        prims.append(pyrender.Primitive(positions=P, normals=N, texcoord_0=UV, indices=idx,
                                        material=g["mat"], poses=poses))
    return prims


def build_scene(stage_path, bg=(0.93, 0.94, 0.96)):
    stage = Usd.Stage.Open(str(stage_path))
    xc = UsdGeom.XformCache()
    scene = pyrender.Scene(bg_color=list(bg) + [1.0], ambient_light=[0.35, 0.35, 0.37])
    # 1) 인스턴스: 프로토타입별 한 번만
    inst = {}
    for p in stage.Traverse():
        if p.IsInstance():
            inst.setdefault(p.GetPrototype(), []).append(_np(xc.GetLocalToWorldTransform(p)))
    stats = {}
    for proto, mats in inst.items():
        parts = _collect(proto, UsdGeom.XformCache(), proxies=True)
        poses = np.stack([m.T for m in mats])  # row-vector → column-vector
        prims = _merge(parts, poses)
        scene.add(pyrender.Mesh(prims))
        stats[str(proto.GetPath())] = (len(mats), sum(len(pr.indices) for pr in prims))
    # 2) 일반 gprim
    parts = _collect(stage.GetPseudoRoot(), xc)
    if parts:
        scene.add(pyrender.Mesh(_merge(parts)))
    # 조명
    for d, it in (((-0.4, -0.6, -1.0), 3.2), ((0.6, 0.3, -0.7), 1.2)):
        dz = -np.array(d) / np.linalg.norm(d)  # 빛 진행 반대 = 카메라 z축
        scene.add(pyrender.DirectionalLight(color=[1, 1, 1], intensity=it), pose=_look_pose(dz * 10, (0, 0, 0)))
    return scene, stats


def _look_pose(eye, target, up=(0, 0, 1)):
    eye, target, up = map(lambda v: np.asarray(v, float), (eye, target, up))
    z = eye - target
    z /= np.linalg.norm(z)
    if abs(np.dot(z, up)) > 0.999:
        up = np.array([0, 1, 0.0])
    x = np.cross(up, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    P = np.eye(4)
    P[:3, 0], P[:3, 1], P[:3, 2], P[:3, 3] = x, y, z, eye
    return P


def render(scene, eye, target, size=(1600, 1000), yfov_deg=40, ortho=None, shadows=True):
    """ortho=(xmag, ymag) 이면 직교 투영."""
    if ortho:
        cam = pyrender.OrthographicCamera(xmag=ortho[0], ymag=ortho[1], znear=0.1, zfar=500)
    else:
        cam = pyrender.PerspectiveCamera(yfov=np.radians(yfov_deg), aspectRatio=size[0] / size[1],
                                         znear=0.1, zfar=500)
    node = scene.add(cam, pose=_look_pose(eye, target))
    r = pyrender.OffscreenRenderer(*size)
    flags = pyrender.RenderFlags.SHADOWS_DIRECTIONAL if shadows else pyrender.RenderFlags.NONE
    color, _ = r.render(scene, flags=flags)
    r.delete()
    scene.remove_node(node)
    return color
