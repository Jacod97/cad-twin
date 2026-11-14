"""Step 2: 설비 목록(equipment.json) → USD 공장 장면.

에셋 로컬 프레임 (확인함, 단위 m, Z-up):
  - 직선 a05: 시작 x=0 → +X 방향 길이 2.0, Y 중앙(-0.575..0.575, 프레임 폭 1.15), 롤러 상면 z=0.769
  - 곡선 a02: 입구 (0,0) +X 방향, 오른쪽(시계방향) 90° 회전, 중심선 반지름 R0=1.4955, 출구 (R0,-R0) -Y 방향
  - 60° 곡선은 에셋이 없어 같은 규약의 단순 곡선판을 out/scene/proc/ 에 만들어 reference

배치 규칙:
  - 직선: 시작점에 놓고 진행 방향으로 회전, X=구간길이/2.0, Y=폭/1.15 스케일
  - 곡선: 오른쪽 회전은 그대로, 왼쪽 회전은 끝점에서 거꾸로 놓은 오른쪽 회전(롤러 모양 동일). XY 균일 스케일 = R/R0
  - 합류/분기 접합 끝은 상대 컨베이어 폭의 절반만큼 잘라 겹침 방지 (직선만)
  - 좌표: 월드(m) = (DXF(mm) - origin_mm) / 1000

출력: out/scene/factory.usda (+ out/scene/proc/*.usda), 콘솔 요약
"""
import json
import math
from pathlib import Path

import numpy as np
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
SCENE_DIR = OUT / "scene"
SCENE = SCENE_DIR / "factory.usda"

STRAIGHT_LEN, STRAIGHT_W = 2.0, 1.15
CURVE_R0 = 1.4955
ROLLER_TOP = 0.769
NS = "cadtwin:"

CFG = yaml.safe_load(open(HERE / "config.yaml"))
EQ = json.load(open(OUT / "equipment.json"))
ORIGIN = np.array(EQ["origin_mm"])
LOG = []
_PROC_CACHE = {}


def w(p_mm):
    p = (np.asarray(p_mm, float) - ORIGIN) / 1000.0
    return p


def heading_of(v):
    return math.degrees(math.atan2(v[1], v[0]))


def rot2(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


# ---------------------------------------------------------------- 절차 생성 곡선 에셋
def make_proc_curve(theta_deg, R, width):
    """a02와 같은 규약(입구 원점 +X, 오른쪽 회전)의 단순 곡선 컨베이어: 상판(회색) + 측면 프레임(파랑) + 다리."""
    R = round(R * 100) / 100      # 10mm 단위로 맞춰 거의 같은 곡선은 에셋 공유
    name = f"curve_a{theta_deg:.0f}_r{R*1000:.0f}_w{width*1000:.0f}"
    path = SCENE_DIR / "proc" / f"{name}.usda"
    if name in _PROC_CACHE:
        return _PROC_CACHE[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    st = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    root = UsdGeom.Xform.Define(st, "/Root")
    st.SetDefaultPrim(root.GetPrim())
    n = max(8, int(theta_deg / 3))
    ang = np.radians(np.linspace(0, theta_deg, n + 1))
    ctr = np.array([0.0, -R])

    def ring(r):  # 오른쪽 회전: 중심(0,-R)에서 각도 90° → 90°-θ
        a = np.pi / 2 - ang
        return np.stack([ctr[0] + r * np.cos(a), ctr[1] + r * np.sin(a)], 1)

    def band(name, r0, r1, z0, z1, color):
        """r0..r1, z0..z1 단면을 호를 따라 쓸어 만든 닫힌 띠."""
        prof = [(r0, z0), (r1, z0), (r1, z1), (r0, z1)]
        pts = []
        for r, z in prof:
            for xy in ring(r):
                pts.append((xy[0], xy[1], z))
        m = n + 1
        faces, counts = [], []
        for k in range(4):
            a0, a1 = k * m, ((k + 1) % 4) * m
            for i in range(n):
                faces += [a0 + i, a1 + i, a1 + i + 1, a0 + i + 1]
                counts.append(4)
        # 양 끝 마개
        for end in (0, n):
            faces += [k * m + end for k in range(4)][::(1 if end else -1)]
            counts.append(4)
        mesh = UsdGeom.Mesh.Define(st, f"/Root/{name}")
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in pts]))
        mesh.CreateFaceVertexCountsAttr(counts)
        mesh.CreateFaceVertexIndicesAttr(faces)
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])

    hw = width / 2
    band("Bed", R - hw + 0.06, R + hw - 0.06, ROLLER_TOP - 0.04, ROLLER_TOP, (0.66, 0.66, 0.68))
    band("FrameIn", R - hw, R - hw + 0.06, ROLLER_TOP - 0.18, ROLLER_TOP + 0.03, (0.32, 0.32, 0.34))
    band("FrameOut", R + hw - 0.06, R + hw, ROLLER_TOP - 0.18, ROLLER_TOP + 0.03, (0.32, 0.32, 0.34))
    # 다리: 중간 각도에서 안/밖 2개
    mid = np.radians(theta_deg / 2)
    for k, r in enumerate((R - hw + 0.03, R + hw - 0.03)):
        a = np.pi / 2 - mid
        c = UsdGeom.Cube.Define(st, f"/Root/Leg{k}")
        c.CreateSizeAttr(1.0)
        c.CreateDisplayColorAttr([Gf.Vec3f(0.32, 0.32, 0.34)])
        x = UsdGeom.XformCommonAPI(c)
        x.SetTranslate(Gf.Vec3d(ctr[0] + r * math.cos(a), ctr[1] + r * math.sin(a), (ROLLER_TOP - 0.18) / 2))
        x.SetScale(Gf.Vec3f(0.05, 0.05, ROLLER_TOP - 0.18))
    st.GetRootLayer().Save()
    _PROC_CACHE[name] = (path, 1.0)   # 이미 R 크기로 만들었으므로 스케일 1
    return _PROC_CACHE[name]


# ---------------------------------------------------------------- 배치 계산
def straight_xform(c, trim_start, trim_end):
    p = np.array(c["polyline_mm"], float)
    seg = np.diff(p, axis=0)
    L = np.hypot(*seg.T)
    d = seg[np.argmax(L)] / L.max()               # 가장 긴 조각 방향 (100mm 꺾임 무시)
    length = float(np.dot(p[-1] - p[0], d))
    start = p[0] + d * trim_start
    length -= trim_start + trim_end
    return dict(origin=w(start), heading=heading_of(d), sx=length / 1000 / STRAIGHT_LEN,
                sy=c["width_mm"] / 1000 / STRAIGHT_W, length_m=length / 1000)


def curve_xform(c):
    """곡선 구간: 회전각을 30° 단위로 맞추고, 현(chord)으로 반지름·방향 계산."""
    turn = c["turn_deg"]
    theta = 30.0 * round(abs(turn) / 30.0)
    if abs(theta - abs(turn)) > 2.0:
        LOG.append(f"NOTE {c['id']}: turn {turn:.1f}° snapped to {theta:.0f}°")
    s, e = np.array(c["start_mm"]), np.array(c["end_mm"])
    if turn < 0:      # 오른쪽 회전: 입구 = 시작점
        a, b = s, e
    else:             # 왼쪽 회전: 끝점에서 거꾸로 놓은 오른쪽 회전
        a, b = e, s
    chord = b - a
    R = np.hypot(*chord) / (2 * math.sin(math.radians(theta) / 2)) / 1000
    h0 = heading_of(chord) + theta / 2
    # 검증: 출구 위치
    th = math.radians(theta)
    exit_local = np.array([R * math.sin(th), -R * (1 - math.cos(th))])
    exit_world = w(a)[:2] + rot2(h0) @ exit_local
    err = np.hypot(*(exit_world - w(b)[:2])) * 1000
    if err > 5:
        LOG.append(f"WARNING {c['id']}: curve end mismatch {err:.0f}mm")
    return dict(origin=w(a), heading=h0, theta=theta, R=R, reversed=turn > 0, end_err_mm=err)


def junction_trims():
    """합류(src 끝)·분기(dst 시작) 쪽 직선 구간을 상대 컨베이어 반폭/sin(각) 만큼 자름."""
    byid = {c["id"]: c for c in EQ["conveyors"]}
    trims = {}
    for l in EQ["links"]:
        if l["type"] not in ("merge", "divert"):
            continue
        br, host = (byid[l["src"]], byid[l["dst"]]) if l["type"] == "merge" else (byid[l["dst"]], byid[l["src"]])
        if br["kind"] != "straight":
            continue
        bd = np.array(br["end_mm"]) - np.array(br["start_mm"])
        hd = np.array(host["end_mm"]) - np.array(host["start_mm"])
        sin = abs(bd[0] * hd[1] - bd[1] * hd[0]) / (np.linalg.norm(bd) * np.linalg.norm(hd))
        t = host["width_mm"] / 2 / max(sin, 0.2)
        key = "end" if l["type"] == "merge" else "start"
        trims.setdefault(br["id"], {"start": 0.0, "end": 0.0})[key] = t
        LOG.append(f"trim {br['id']} {key} by {t:.0f}mm ({l['type']} with {host['id']})")
    return trims


# ---------------------------------------------------------------- 제어반 겹침 해소
def trimmed_poly(c, trims):
    """접합 trim을 반영한 중심선 (겹침 검사용)."""
    p = np.array(c["polyline_mm"], float)
    t = trims.get(c["id"])
    if not t:
        return p
    def cut_front(p, d):
        while d > 0 and len(p) > 1:
            L = np.hypot(*(p[1] - p[0]))
            if L > d:
                p = p.copy(); p[0] = p[0] + (p[1] - p[0]) * d / L
                return p
            d -= L; p = p[1:]
        return p
    p = cut_front(p, t["start"])
    p = cut_front(p[::-1], t["end"])[::-1]
    return p


def clear_panel(cp, trims):
    """제어반 외곽이 컨베이어 폭 안에 들어가면 가장 가까운 컨베이어 중심선에서 멀어지는 방향으로 밀어냄."""
    o = np.array(cp["outline_mm"])
    mn, mx = o.min(0), o.max(0)
    ctr, half = (mn + mx) / 2, (mx - mn) / 2
    gap = CFG["control_panel"]["clearance_mm"]
    moved = np.zeros(2)
    for _ in range(4):
        worst = None
        for c in EQ["conveyors"]:
            p = trimmed_poly(c, trims)
            for a, b in zip(p[:-1], p[1:]):
                d = b - a
                L2 = float(d @ d)
                if L2 == 0:
                    continue
                t = (ctr + moved - a) @ d / L2
                if not (0.0 < t < 1.0):   # 구간 이음매의 끝점 기준 대각 밀림 방지: 수직 투영만 사용
                    continue
                q = a + t * d
                v = ctr + moved - q
                dist = np.linalg.norm(v)
                nrm = v / (dist + 1e-9)
                ext = abs(nrm[0]) * half[0] + abs(nrm[1]) * half[1]
                need = c["width_mm"] / 2 + gap - (dist - ext)
                if need > 0 and (worst is None or need > worst[0]):
                    worst = (need, nrm, c["id"])
        if worst is None:
            break
        moved += worst[1] * worst[0]
    return moved


# ---------------------------------------------------------------- 입체 교차 높이
def elevations(trims):
    """구간별 (시작 높이 오프셋, 끝 높이 오프셋) m. 교차하는 elevated_line 구간은 평평하게 높이고,
    앞뒤 n개 구간은 수평 길이에 비례해 선형 경사. 측면 이송으로 붙은 평행 레인은 짝 구간과 같은 높이."""
    g = CFG.get("grade_separation", {})
    elev = {c["id"]: (0.0, 0.0) for c in EQ["conveyors"]}
    if not g.get("enabled"):
        return elev
    byid = {c["id"]: c for c in EQ["conveyors"]}
    dz = float(g["roller_top_m"]) - ROLLER_TOP
    up, down, side = {}, {}, {}
    for l in EQ["links"]:
        if l["type"] == "inline":
            down[l["src"]] = l["dst"]; up[l["dst"]] = l["src"]
        elif l["type"] == "side_transfer":
            side[l["src"]] = l["dst"]
    def hlen(i):
        t = trims.get(i, {"start": 0.0, "end": 0.0})
        return straight_xform(byid[i], t["start"], t["end"])["length_m"]
    def chain(start, nxt, n):
        out, cur = [], start
        while len(out) < n and cur in nxt and byid[nxt[cur]]["line"] == byid[start]["line"]:
            cur = nxt[cur]; out.append(cur)
        return out
    for x in EQ["crossings"]:
        e = x["a"] if byid[x["a"]]["line"] == g["elevated_line"] else x["b"]
        if byid[e]["line"] != g["elevated_line"]:
            continue
        elev[e] = (dz, dz)
        before = chain(e, up, int(g["ramp_sections_before"]))[::-1]     # 상류 → 교차 순
        after = chain(e, down, int(g["ramp_sections_after"]))
        for seq, z_from, z_to in ((before, 0.0, dz), (after, dz, 0.0)):
            tot = sum(hlen(i) for i in seq)
            acc = 0.0
            for i in seq:
                a = z_from + (z_to - z_from) * acc / tot
                acc += hlen(i)
                elev[i] = (a, z_from + (z_to - z_from) * acc / tot)
        for i in after:                      # 평행 레인: 짝 구간과 같은 높이
            if i in side:
                a, b = i, side[i]
                while True:
                    elev[b] = elev[a]
                    if a in down and b in down:
                        a, b = down[a], down[b]
                    else:
                        break
        ang = max(abs(np.degrees(np.arctan2(elev[i][1] - elev[i][0], hlen(i)))) for i in before + after)
        LOG.append(f"grade separation: {e} raised +{dz:.2f}m, ramps {before} / {after}, max slope {ang:.1f} deg")
    return elev


# ---------------------------------------------------------------- USD 작성
def set_attrs(prim, d):
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, bool):
            t = Sdf.ValueTypeNames.Bool
        elif isinstance(v, int):
            t = Sdf.ValueTypeNames.Int
        elif isinstance(v, float):
            t = Sdf.ValueTypeNames.Double
        elif isinstance(v, (list, tuple)) and (not v or isinstance(v[0], str)):
            t = Sdf.ValueTypeNames.StringArray
        elif isinstance(v, (list, tuple)):
            t = Sdf.ValueTypeNames.Double2 if len(v) == 2 else Sdf.ValueTypeNames.Double3
            v = tuple(float(x) for x in v)
        else:
            t, v = Sdf.ValueTypeNames.String, str(v)
        prim.CreateAttribute(NS + k, t, custom=True).Set(v)


def place(stage, path, ref_file, origin, heading, scale, z0=0.0, z1=0.0, hlen=None):
    """z0/z1: 시작/끝 높이 오프셋(m). 다르면 롤러 상면이 이어지도록 롤러 상면 시작점을 축으로 기울임."""
    xf = UsdGeom.Xform.Define(stage, path)
    api = UsdGeom.XformCommonAPI(xf)
    pitch, tz, ox, oy = 0.0, z0, float(origin[0]), float(origin[1])
    scale = list(scale)
    if abs(z1 - z0) > 1e-9:
        pitch = math.atan2(z1 - z0, hlen)
        scale[0] *= math.hypot(hlen, z1 - z0) / hlen          # 경사 길이만큼 늘림
        hx = -ROLLER_TOP * math.sin(pitch)                     # 기울인 뒤 롤러 상면 시작점의 이동량 보정
        hr = math.radians(heading)
        ox -= hx * math.cos(hr); oy -= hx * math.sin(hr)
        tz = z0 + ROLLER_TOP - ROLLER_TOP * math.cos(pitch)
    api.SetTranslate(Gf.Vec3d(ox, oy, tz))
    api.SetRotate(Gf.Vec3f(0, -math.degrees(pitch), float(heading)))   # XYZ 순서: Y(경사) 먼저, Z(방향) 나중
    api.SetScale(Gf.Vec3f(*[float(s) for s in scale]))
    geom = stage.DefinePrim(f"{path}/Geom")
    rel = Path(ref_file).resolve()
    geom.GetReferences().AddReference(str(Path(_relpath(rel, SCENE_DIR))))
    geom.SetInstanceable(True)
    return xf


def _relpath(p, base):
    import os
    return os.path.relpath(p, base)


def main():
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    if SCENE.exists():
        SCENE.unlink()
    stage = Usd.Stage.CreateNew(str(SCENE))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    set_attrs(world.GetPrim(), dict(source_dxf=EQ["source"], origin_mm=EQ["origin_mm"],
                                    note="world(m) = (dxf(mm) - origin_mm)/1000"))

    straight_ref = ROOT / CFG["assets"]["straight"]
    curve_ref = ROOT / CFG["assets"]["curve90"]
    trims = junction_trims()
    elev = elevations(trims)
    outs, ins = {}, {}
    for l in EQ["links"]:
        outs.setdefault(l["src"], []).append(l["dst"])
        ins.setdefault(l["dst"], []).append(l["src"])
    cross = {}
    for c in EQ["crossings"]:
        cross.setdefault(c["a"], []).append(c["b"])
        cross.setdefault(c["b"], []).append(c["a"])

    UsdGeom.Scope.Define(stage, "/World/Conveyors")
    counts = {}
    supports = []
    for c in EQ["conveyors"]:
        line = f"/World/Conveyors/L{c['line'][:2]}xx"
        if not stage.GetPrimAtPath(line):
            UsdGeom.Scope.Define(stage, line)
        path = f"{line}/{c['id']}"
        attrs = dict(id=c["id"], tag=int(c["tag"]), line=c["line"], kind=c["kind"],
                     length_mm=float(c["length_mm"]), width_mm=float(c["width_mm"]),
                     dxf_start_mm=c["start_mm"], dxf_end_mm=c["end_mm"],
                     downstream=outs.get(c["id"], []), upstream=ins.get(c["id"], []),
                     crosses=cross.get(c["id"], []))
        if c["kind"] == "straight":
            t = trims.get(c["id"], {"start": 0.0, "end": 0.0})
            x = straight_xform(c, t["start"], t["end"])
            z0, z1 = elev[c["id"]]
            xf = place(stage, path, straight_ref, x["origin"], x["heading"], (x["sx"], x["sy"], 1.0),
                       z0, z1, x["length_m"])
            if max(z0, z1) > 0:
                attrs.update(elev_start_m=float(z0), elev_end_m=float(z1),
                             slope_deg=float(np.degrees(np.arctan2(z1 - z0, x["length_m"]))))
                supports.append((c, x, z0, z1))
            attrs.update(asset=CFG["assets"]["straight"], placed_length_m=x["length_m"],
                         trim_start_mm=t["start"], trim_end_mm=t["end"])
            key = "straight(a05)"
        else:
            x = curve_xform(c)
            if abs(x["theta"] - 90) < 1e-6:
                s = x["R"] / CURVE_R0
                xf = place(stage, path, curve_ref, x["origin"], x["heading"], (s, s, 1.0))
                attrs.update(asset=CFG["assets"]["curve90"], placed_width_mm=1150 * s)
                key = "curve90(a02)"
            else:
                pf, _ = make_proc_curve(x["theta"], x["R"], c["width_mm"] / 1000)
                xf = place(stage, path, pf, x["origin"], x["heading"], (1.0, 1.0, 1.0))
                attrs.update(asset=str(pf.relative_to(HERE)), placed_width_mm=c["width_mm"])
                key = f"curve{x['theta']:.0f}(procedural)"
            attrs.update(curve_deg=x["theta"], curve_radius_m=x["R"], placed_reversed=x["reversed"],
                         end_error_mm=x["end_err_mm"])
            if abs(attrs.get("placed_width_mm", c["width_mm"]) - c["width_mm"]) > 5:
                LOG.append(f"NOTE {c['id']}: curve asset width {attrs['placed_width_mm']:.0f}mm "
                           f"(config {c['width_mm']:.0f}mm) - 곡선은 XY 균일 스케일이라 폭을 따로 못 맞춤")
        attrs["asset_ref"] = attrs.pop("asset")
        set_attrs(xf.GetPrim(), attrs)
        counts[key] = counts.get(key, 0) + 1

    # 높인 구간·경사로 아래 지지 기둥 (다른 라인 컨베이어 위는 건너뜀)
    UsdGeom.Scope.Define(stage, "/World/Supports")
    sp = float(CFG.get("grade_separation", {}).get("support_spacing_m", 1.2))
    n_sup = 0
    others = [(trimmed_poly(o, trims), o["width_mm"], o["line"]) for o in EQ["conveyors"]]
    for c, x, z0, z1 in supports:
        hr = math.radians(x["heading"])
        d, nrm = np.array([math.cos(hr), math.sin(hr)]), np.array([-math.sin(hr), math.cos(hr)])
        n = max(1, int(math.ceil(x["length_m"] / sp)))
        for k in range(n + 1):
            f = k / n
            # 구간 이음매에서는 앞 구간만 기둥을 세움 (중복 방지), 단 첫 구간 시작은 포함
            if k == 0 and z0 > 0 and any(s[0]["id"] in ins.get(c["id"], []) for s in supports):
                continue
            top = z0 + (z1 - z0) * f + 0.62
            if top < 0.9:
                continue
            ctr = x["origin"][:2] + d * x["length_m"] * f
            for side in (-1, 1):
                p = ctr + nrm * side * (c["width_mm"] / 2000 - 0.04)
                pm = p * 1000 + ORIGIN
                blocked = False
                for poly, wo, line in others:
                    if line == c["line"]:
                        continue
                    for a, b in zip(poly[:-1], poly[1:]):
                        ab = b - a
                        if ab @ ab < 1e-9:
                            continue
                        t = np.clip((pm - a) @ ab / (ab @ ab), 0, 1)
                        if np.linalg.norm(pm - (a + t * ab)) < wo / 2 + 100:
                            blocked = True
                            break
                    if blocked:
                        break
                if blocked:
                    continue
                cube = UsdGeom.Cube.Define(stage, f"/World/Supports/{c['id']}_S{k}{'L' if side < 0 else 'R'}")
                cube.CreateSizeAttr(1.0)
                api = UsdGeom.XformCommonAPI(cube)
                api.SetTranslate(Gf.Vec3d(float(p[0]), float(p[1]), top / 2))
                api.SetScale(Gf.Vec3f(0.08, 0.08, float(top)))
                cube.CreateDisplayColorAttr([Gf.Vec3f(0.30, 0.30, 0.32)])
                set_attrs(cube.GetPrim(), dict(supports=c["id"]))
                n_sup += 1
    LOG.append(f"support posts: {n_sup}")

    # 제어반
    UsdGeom.Scope.Define(stage, "/World/ControlPanels")
    hcfg = CFG["control_panel"]["height_mm"]
    for cp in EQ["control_panels"]:
        o = np.array(cp["outline_mm"])
        mn, mx = o.min(0), o.max(0)
        moved = clear_panel(cp, trims) if CFG["control_panel"]["clear_overlap"] else np.zeros(2)
        if np.linalg.norm(moved) > 1:
            LOG.append(f"moved {cp['id']} by ({moved[0]:.0f},{moved[1]:.0f})mm to clear conveyor")
        ctr = (mn + mx) / 2 + moved
        H = hcfg.get(cp["block"], hcfg["default"]) / 1000
        size = (mx - mn) / 1000
        path = f"/World/ControlPanels/{cp['id']}"
        xf = UsdGeom.Xform.Define(stage, path)
        p = w(ctr)
        UsdGeom.XformCommonAPI(xf).SetTranslate(Gf.Vec3d(float(p[0]), float(p[1]), H / 2))
        cube = UsdGeom.Cube.Define(stage, path + "/Body")
        cube.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(cube).SetScale(Gf.Vec3f(float(size[0]), float(size[1]), float(H)))
        cube.CreateDisplayColorAttr([Gf.Vec3f(0.85, 0.82, 0.70)])
        # 문 방향 표시용 얇은 판 (앞면)
        if cp["door_dir"]:
            dd = np.array(cp["door_dir"])
            ax = np.argmax(np.abs(dd))
            off = np.zeros(2)
            off[ax] = np.sign(dd[ax]) * (size[ax] / 2 + 0.005)
            door = UsdGeom.Cube.Define(stage, path + "/DoorFace")
            door.CreateSizeAttr(1.0)
            sc = [size[0] * 0.9, size[1] * 0.9, H * 0.85]
            sc[ax] = 0.01
            api = UsdGeom.XformCommonAPI(door)
            api.SetTranslate(Gf.Vec3d(float(off[0]), float(off[1]), 0.0))
            api.SetScale(Gf.Vec3f(*[float(v) for v in sc]))
            door.CreateDisplayColorAttr([Gf.Vec3f(0.55, 0.55, 0.58)])
        set_attrs(xf.GetPrim(), dict(id=cp["id"], block=cp["block"], nominal_mm=cp["nominal_mm"],
                                     height_mm=H * 1000, dxf_center_mm=cp["center_mm"],
                                     moved_mm=[float(moved[0]), float(moved[1])],
                                     nearest_conveyor=cp["nearest_conveyor"], dxf_handle=cp["dxf_handle"]))

    # IoT
    UsdGeom.Scope.Define(stage, "/World/IoT")
    zr = CFG["iot"]["marker_height_mm"] / 1000
    rr = CFG["iot"]["marker_radius_mm"] / 1000
    for d in EQ["iot"]:
        p = w(d["pos_mm"])
        z0, z1 = elev[d["conveyor"]]
        dzi = 0.0
        if max(z0, z1) > 0:      # 높인 구간의 센서는 컨베이어를 따라 올림
            cv = next(c for c in EQ["conveyors"] if c["id"] == d["conveyor"])
            a, b = np.array(cv["start_mm"]), np.array(cv["end_mm"])
            f = float(np.clip((np.array(d["pos_mm"]) - a) @ (b - a) / ((b - a) @ (b - a)), 0, 1))
            dzi = z0 + (z1 - z0) * f
        sph = UsdGeom.Sphere.Define(stage, f"/World/IoT/{d['id']}")
        sph.CreateRadiusAttr(rr)
        UsdGeom.XformCommonAPI(sph).SetTranslate(Gf.Vec3d(float(p[0]), float(p[1]), zr + dzi))
        sph.CreateDisplayColorAttr([Gf.Vec3f(1.0, 0.75, 0.0)])
        set_attrs(sph.GetPrim(), dict(id=d["id"], conveyor=d["conveyor"], dxf_pos_mm=d["pos_mm"],
                                      dxf_handle=d["dxf_handle"]))

    # 바닥
    allp = np.vstack([w(c["polyline_mm"]) for c in EQ["conveyors"]])
    mn, mx = allp.min(0) - 3, allp.max(0) + 3
    floor = UsdGeom.Mesh.Define(stage, "/World/Floor")
    floor.CreatePointsAttr([Gf.Vec3f(float(mn[0]), float(mn[1]), 0), Gf.Vec3f(float(mx[0]), float(mn[1]), 0),
                            Gf.Vec3f(float(mx[0]), float(mx[1]), 0), Gf.Vec3f(float(mn[0]), float(mx[1]), 0)])
    floor.CreateFaceVertexCountsAttr([4])
    floor.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    floor.CreateDisplayColorAttr([Gf.Vec3f(0.62, 0.62, 0.60)])
    stage.GetRootLayer().Save()

    print("\n".join(LOG))
    print(f"\nconveyors placed: {counts}")
    print(f"control panels: {len(EQ['control_panels'])}, iot: {len(EQ['iot'])}")
    print("wrote", SCENE)


if __name__ == "__main__":
    main()
