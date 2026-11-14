"""Step 5: 결과 색 장면(factory_sim.usda) → 맥 미리보기(Quick Look)로 볼 수 있는 .usdz

맥 Quick Look(RealityKit)에 맞춘 변환
  - Cube/Sphere 같은 암시적 도형 → Mesh
  - 색은 displayColor 대신 UsdPreviewSurface 재질 (MDL 셰이더는 버림)
  - 텍스처는 1024px JPEG로 줄여 패키지에 포함
  - Z-up → Y-up (루트를 X축 -90° 회전, upAxis=Y), 단위 m 유지
  - 같은 에셋은 한 번만 넣고 instanceable reference로 재사용 (파일 크기·메모리 절약)

모드
  full : SimReady 에셋 형상 그대로 (컨베이어 1구간 ≈ 21만 삼각형, 인스턴싱 미지원 환경에서는 무거움)
  lite : 컨베이어를 단순 프레임(회색 판+다리)으로 대체. 결과 색 띠·제어반·지지대·IoT는 동일

출력: out/factory_sim_full.usdz, out/factory_sim_lite.usdz (+ 각 ARKit 호환성 검사 결과 출력)
사용: python s5_export_usdz.py [full|lite|both]
"""
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
SRC = OUT / "scene" / "factory_sim.usda"
BUILD = OUT / "usdz_build"
TEX_MAX = 1024
sys.path.insert(0, str(HERE))


# ---------------------------------------------------------------- 형상 → 배열
def _box(size):
    h = size / 2
    P = np.array([[-h, -h, -h], [h, -h, -h], [h, h, -h], [-h, h, -h],
                  [-h, -h, h], [h, -h, h], [h, h, h], [-h, h, h]])
    F = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [3, 0, 4, 7]]
    return P, [4] * 6, sum(F, [])


def _sphere(r, nu=12, nv=8):
    P = [[0, 0, r]]
    for i in range(1, nv):
        th = math.pi * i / nv
        for j in range(nu):
            ph = 2 * math.pi * j / nu
            P.append([r * math.sin(th) * math.cos(ph), r * math.sin(th) * math.sin(ph), r * math.cos(th)])
    P.append([0, 0, -r])
    counts, idx = [], []
    for j in range(nu):
        counts.append(3); idx += [0, 1 + j, 1 + (j + 1) % nu]
    for i in range(nv - 2):
        a, b = 1 + i * nu, 1 + (i + 1) * nu
        for j in range(nu):
            counts.append(4); idx += [a + j, b + j, b + (j + 1) % nu, a + (j + 1) % nu]
    last = len(P) - 1
    b = 1 + (nv - 2) * nu
    for j in range(nu):
        counts.append(3); idx += [last, b + (j + 1) % nu, b + j]
    return np.array(P), counts, idx


def gprim_arrays(prim, M):
    """gprim → dict(points, counts, indices, normals, n_interp, st, st_interp, st_indices), M(row-vector) 적용."""
    R = M[:3, :3]
    xf = lambda P: (np.c_[P, np.ones(len(P))] @ M)[:, :3]
    if prim.IsA(UsdGeom.Mesh):
        m = UsdGeom.Mesh(prim)
        P = np.array(m.GetPointsAttr().Get() or [], float)
        if len(P) == 0:
            return None
        d = dict(points=xf(P), counts=list(m.GetFaceVertexCountsAttr().Get()),
                 indices=list(m.GetFaceVertexIndicesAttr().Get()),
                 left=m.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded)
        N = m.GetNormalsAttr().Get()
        if N:
            N = np.array(N, float) @ np.linalg.inv(R).T
            d["normals"] = N / (np.linalg.norm(N, axis=1, keepdims=True) + 1e-12)
            d["n_interp"] = m.GetNormalsInterpolation()
        pv = UsdGeom.PrimvarsAPI(prim)
        for name in ("st", "st0", "UVMap"):
            if pv.HasPrimvar(name):
                p = pv.GetPrimvar(name)
                d["st"] = np.array(p.Get(), float)
                d["st_interp"] = p.GetInterpolation()
                d["st_indices"] = list(p.GetIndices()) if p.IsIndexed() else None
                break
        return d
    if prim.IsA(UsdGeom.Cube):
        P, c, i = _box(UsdGeom.Cube(prim).GetSizeAttr().Get())
    elif prim.IsA(UsdGeom.Sphere):
        P, c, i = _sphere(UsdGeom.Sphere(prim).GetRadiusAttr().Get())
    else:
        return None
    return dict(points=xf(P), counts=c, indices=i, left=bool(np.linalg.det(R) < 0))


# ---------------------------------------------------------------- 재질
class Materials:
    def __init__(self, stage, root):
        self.stage, self.root, self.cache = stage, root, {}
        UsdGeom.Scope.Define(stage, f"{root}/Materials")
        (BUILD / "tex").mkdir(parents=True, exist_ok=True)

    def _surface(self, name):
        mat = UsdShade.Material.Define(self.stage, f"{self.root}/Materials/{name}")
        sh = UsdShade.Shader.Define(self.stage, f"{self.root}/Materials/{name}/Surface")
        sh.CreateIdAttr("UsdPreviewSurface")
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)
        sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        return mat, sh

    def color(self, rgb):
        key = "C_" + "".join(f"{int(round(max(0, min(1, v)) * 255)):02x}" for v in rgb)
        if key not in self.cache:
            mat, sh = self._surface(key)
            sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*[float(v) for v in rgb]))
            self.cache[key] = mat
        return self.cache[key]

    def texture(self, src_path):
        stem = Path(src_path).stem
        key = "T_" + "".join(ch if ch.isalnum() else "_" for ch in stem)
        if key not in self.cache:
            dst = BUILD / "tex" / f"{stem}.jpg"
            if not dst.exists():
                im = Image.open(src_path).convert("RGB")
                im.thumbnail((TEX_MAX, TEX_MAX))
                im.save(dst, quality=85)
            mat, sh = self._surface(key)
            st = UsdShade.Shader.Define(self.stage, f"{self.root}/Materials/{key}/st")
            st.CreateIdAttr("UsdPrimvarReader_float2")
            st.CreateInput("varname", Sdf.ValueTypeNames.String).Set("st")
            tx = UsdShade.Shader.Define(self.stage, f"{self.root}/Materials/{key}/Albedo")
            tx.CreateIdAttr("UsdUVTexture")
            tx.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(f"./usdz_build/tex/{stem}.jpg"))
            tx.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st.ConnectableAPI(), "result")
            tx.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
            tx.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
            tx.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            tx.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tx.ConnectableAPI(), "rgb")
            self.cache[key] = mat
        return self.cache[key]

    def for_prim(self, prim):
        """원본 prim의 UsdPreviewSurface diffuse(텍스처/상수) 또는 displayColor → 새 재질."""
        mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        if mat:
            for ch in Usd.PrimRange(mat.GetPrim()):
                sh = UsdShade.Shader(ch)
                if not sh or sh.GetIdAttr().Get() != "UsdPreviewSurface":
                    continue
                inp = sh.GetInput("diffuseColor")
                if inp:
                    src = inp.GetConnectedSources()[0]
                    if src:
                        f = UsdShade.Shader(src[0].source.GetPrim()).GetInput("file")
                        p = f.Get().resolvedPath if f and f.Get() else ""
                        if p and Path(p).exists():
                            return self.texture(p)
                    elif inp.Get() is not None:
                        return self.color(list(inp.Get()))
                break
        g = UsdGeom.Gprim(prim)
        c = g.GetDisplayColorAttr().Get() if g else None
        return self.color(list(c[0]) if c else [0.7, 0.7, 0.7])


def emit_mesh(stage, path, d, mat, double_sided=False):
    m = UsdGeom.Mesh.Define(stage, path)
    m.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in d["points"]]))
    m.CreateFaceVertexCountsAttr(Vt.IntArray([int(c) for c in d["counts"]]))
    m.CreateFaceVertexIndicesAttr(Vt.IntArray([int(i) for i in d["indices"]]))
    m.CreateSubdivisionSchemeAttr("none")
    if d.get("left"):
        m.CreateOrientationAttr(UsdGeom.Tokens.leftHanded)
    if "normals" in d:
        m.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(*map(float, n)) for n in d["normals"]]))
        m.SetNormalsInterpolation(d["n_interp"])
    if "st" in d:
        pv = UsdGeom.PrimvarsAPI(m).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, d["st_interp"])
        pv.Set(Vt.Vec2fArray([Gf.Vec2f(*map(float, t)) for t in d["st"]]))
        if d.get("st_indices"):
            pv.SetIndices(Vt.IntArray(d["st_indices"]))
    if double_sided:
        m.CreateDoubleSidedAttr(True)
    lo, hi = d["points"].min(0), d["points"].max(0)
    m.CreateExtentAttr([Gf.Vec3f(*map(float, lo)), Gf.Vec3f(*map(float, hi))])
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    return m


def safe(name):
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)


# ---------------------------------------------------------------- lite 컨베이어
def lite_conveyors(stage, root, mats):
    """에셋 대신 회색 프레임 판 + 다리. 경사·곡선은 결과 색 띠와 같은 중심선·높이를 따름."""
    from s4c_color_scene import ribbon, section_elev, section_polys
    polys, elev = section_polys(), section_elev()
    frame = mats.color((0.36, 0.37, 0.40))
    leg = mats.color((0.26, 0.27, 0.30))
    UsdGeom.Scope.Define(stage, f"{root}/Conveyors")
    for cid, (P, W) in polys.items():
        z0, z1 = elev[cid]
        cl = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(P, axis=0).T))])
        zt = 0.79 + z0 + (z1 - z0) * cl / cl[-1]
        a, b = ribbon(P, W)
        n = len(P)
        pts = np.vstack([np.c_[a, zt], np.c_[b, zt], np.c_[a, zt - 0.15], np.c_[b, zt - 0.15]])
        counts, idx = [], []
        for i in range(n - 1):
            for q in ([i, i + 1, n + i + 1, n + i],                        # 윗면
                      [2 * n + i, 3 * n + i, 3 * n + i + 1, 2 * n + i + 1],  # 아랫면
                      [i, 2 * n + i, 2 * n + i + 1, i + 1],                # 옆면 a
                      [n + i, n + i + 1, 3 * n + i + 1, 3 * n + i]):       # 옆면 b
                counts.append(4); idx += q
        emit_mesh(stage, f"{root}/Conveyors/{cid}", dict(points=pts, counts=counts, indices=idx), frame,
                  double_sided=True)
        # 다리: 양끝(+긴 구간은 중간)에 좌우 한 쌍
        L = cl[-1]
        for k, f in enumerate([0.15, 0.85] if L < 2.5 else [0.1, 0.5, 0.9]):
            s = f * L
            p = np.array([np.interp(s, cl, P[:, 0]), np.interp(s, cl, P[:, 1])])
            z = 0.79 + z0 + (z1 - z0) * f - 0.15
            j = min(int(np.searchsorted(cl, s)), n - 1)
            d = P[min(j, n - 1)] - P[max(j - 1, 0)]
            d /= np.linalg.norm(d) + 1e-12
            nrm = np.array([-d[1], d[0]])
            for side in (-1, 1):
                c = p + nrm * side * (W / 2 - 0.05)
                Pb, cb, ib = _box(1.0)
                Pb = Pb * [0.05, 0.05, z] + [c[0], c[1], z / 2]
                emit_mesh(stage, f"{root}/Conveyors/{cid}_leg{k}{'L' if side < 0 else 'R'}",
                          dict(points=Pb, counts=cb, indices=ib), leg)


# ---------------------------------------------------------------- 메인
def export(mode):
    src = Usd.Stage.Open(str(SRC))
    xc = UsdGeom.XformCache()
    tmp = OUT / f"factory_sim_{mode}.usdc"
    if tmp.exists():
        tmp.unlink()
    st = Usd.Stage.CreateNew(str(tmp))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    root = "/Root"
    r = UsdGeom.Xform.Define(st, root)
    UsdGeom.XformCommonAPI(r).SetRotate(Gf.Vec3f(-90, 0, 0))   # Z-up → Y-up
    st.SetDefaultPrim(r.GetPrim())
    mats = Materials(st, root)
    stats = dict(prototypes=0, instances=0, meshes=0)

    # 1) 인스턴스 (컨베이어 에셋) : 프로토타입 1번 + instanceable 참조
    proto_path = {}
    if mode == "full":
        UsdGeom.Scope.Define(st, f"{root}/Conveyors")
        for p in src.Traverse():
            if not p.IsInstance():
                continue
            proto = p.GetPrototype()
            key = str(proto.GetPath())
            if key not in proto_path:
                cls = f"/Proto_{len(proto_path)}"
                st.CreateClassPrim(cls)
                UsdGeom.Xform.Define(st, cls)
                st.GetPrimAtPath(cls).SetSpecifier(Sdf.SpecifierClass)
                pxc = UsdGeom.XformCache()
                k = 0
                for q in Usd.PrimRange(proto, Usd.TraverseInstanceProxies()):
                    if not (q.IsA(UsdGeom.Mesh) or q.IsA(UsdGeom.Cube) or q.IsA(UsdGeom.Sphere)):
                        continue
                    d = gprim_arrays(q, np.array(pxc.GetLocalToWorldTransform(q)))
                    if d is None:
                        continue
                    emit_mesh(st, f"{cls}/m{k:03d}", d, mats.for_prim(q))
                    k += 1
                proto_path[key] = cls
                stats["prototypes"] += 1
                stats["meshes"] += k
            parent = p.GetParent()
            M = np.array(xc.GetLocalToWorldTransform(p))
            dst = f"{root}/Conveyors/{safe(parent.GetName())}"
            x = UsdGeom.Xform.Define(st, dst)
            x.AddTransformOp().Set(Gf.Matrix4d(M.tolist()))
            g = st.DefinePrim(f"{dst}/Geom")
            g.GetReferences().AddInternalReference(proto_path[key])
            g.SetInstanceable(True)
            stats["instances"] += 1
    else:
        lite_conveyors(st, root, mats)

    # 2) 나머지 gprim (색 띠·병목 표시·제어반·지지대·IoT·바닥) → 월드 좌표 메시
    UsdGeom.Scope.Define(st, f"{root}/Static")
    n = 0
    for q in src.Traverse():
        if q.IsInstance() or q.IsInstanceProxy() or q.IsInPrototype():
            continue
        if not (q.IsA(UsdGeom.Mesh) or q.IsA(UsdGeom.Cube) or q.IsA(UsdGeom.Sphere)):
            continue
        d = gprim_arrays(q, np.array(xc.GetLocalToWorldTransform(q)))
        if d is None:
            continue
        name = safe(str(q.GetPath()).replace("/World/", ""))
        emit_mesh(st, f"{root}/Static/{name}", d, mats.for_prim(q),
                  double_sided=q.GetPath().HasPrefix("/World/SimOverlay") or q.GetName() == "Floor")
        n += 1
    stats["static_meshes"] = n
    st.GetRootLayer().Save()

    # 3) usdz 패키징 + ARKit 호환성 검사
    usdz = OUT / f"factory_sim_{mode}.usdz"
    if usdz.exists():
        usdz.unlink()
    ok = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(tmp)), str(usdz))
    try:
        from pxr.UsdUtils.complianceChecker import ComplianceChecker
        chk = ComplianceChecker(arkit=True)
        chk.CheckCompliance(str(usdz))
        errs, warns = chk.GetErrors(), chk.GetWarnings()
    except Exception as ex:  # 검사 도구가 없는 빌드
        errs, warns = [f"checker unavailable: {ex}"], []
    import zipfile
    names = zipfile.ZipFile(usdz).namelist()
    print(f"[{mode}] usdz contents: {len(names)} files, textures={sum(n.endswith('.jpg') for n in names)}")
    print(f"[{mode}] packaged={ok} {usdz} {usdz.stat().st_size/1e6:.1f} MB  {stats}  materials={len(mats.cache)}")
    print(f"[{mode}] ARKit check: {len(errs)} errors, {len(warns)} warnings")
    for e in list(dict.fromkeys(errs))[:8]:
        print("   ERR ", e[:220])
    for wmsg in list(dict.fromkeys(warns))[:5]:
        print("   WARN", wmsg[:220])
    return usdz


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    if BUILD.exists():
        shutil.rmtree(BUILD)
    for m in (["full", "lite"] if mode == "both" else [mode]):
        export(m)


if __name__ == "__main__":
    main()
