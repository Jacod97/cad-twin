"""Step 4c: 시뮬레이션 결과를 USD 장면 색으로 표시 + 확인 이미지.

출력
  out/scene/factory_sim.usda : factory.usda를 sublayer로 두고
      - 각 컨베이어 prim에 sim:* 속성 (throughput_per_h, avg_wait_s, utilization, bottleneck) 기록 (over)
      - /World/SimOverlay 아래 구간별 색 띠(롤러 위, 점유율 색) + 병목 표시 기둥(빨강)
  out/s4_view_*.png          : 3D 렌더 (색 띠 포함)
  out/s4_top_utilization.png : 위에서 본 렌더 + 색 막대
  out/s4_map_metrics.png     : 루프·레인 영역 2D 지도 3장 (처리량 / 평균 대기 / 점유율), 병목 표시

색 (사용자 지정): 점유율·대기 = 초록→노랑→빨강, 병목·정체 구간은 빨강 고정 (+ 빨간 기둥, 범례 라벨).
    처리량은 많다고 나쁜 게 아니므로 파랑 단색 램프 유지
"""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Polygon
from PIL import Image
from pxr import Gf, Sdf, Usd, UsdGeom

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
sys.path.insert(0, str(HERE))
import usd_render  # noqa: E402
from s2_build_scene import elevations, junction_trims, trimmed_poly  # noqa: E402
from s2_render import VIEWS  # noqa: E402

SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
       "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
CMAP_SEQ = LinearSegmentedColormap.from_list("seq_blue", SEQ)
CMAP = LinearSegmentedColormap.from_list("gyr", ["#0ca30c", "#fab219", "#d03b3b"])   # 초록-노랑-빨강
CRITICAL = "#d03b3b"
INK, INK2, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"

EQ = json.load(open(OUT / "equipment.json"))
RES = json.load(open(OUT / "sim_results.json"))
SEC = {r["id"]: r for r in RES["sections"]}
ORIGIN = np.array(EQ["origin_mm"])
METRICS = [("throughput_per_h", "Throughput (items/h)"),
           ("avg_wait_s", "Avg wait to enter next section (s)"),
           ("utilization", "Utilization (mean occupancy / capacity)")]
COLOR_METRIC = "utilization"   # 3D 장면 색에 쓰는 지표


def w(mm):
    return (np.asarray(mm, float) - ORIGIN) / 1000.0


def ribbon(poly_m, width):
    p = np.asarray(poly_m, float)
    d = np.gradient(p, axis=0)
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-12
    n = np.stack([-d[:, 1], d[:, 0]], 1)
    return p + n * width / 2, p - n * width / 2


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def color_of(r, metric, nrm):
    """병목·정체 구간은 빨강 고정, 나머지는 지표값으로."""
    if metric != "throughput_per_h" and (r["bottleneck"] or r["congested"]):
        return hex2rgb(CRITICAL)
    return (CMAP_SEQ if metric == "throughput_per_h" else CMAP)(nrm(r[metric]))[:3]


def section_elev():
    return elevations(junction_trims())


def section_polys():
    trims = junction_trims()
    out = {}
    for c in EQ["conveyors"]:
        p = trimmed_poly(c, trims) if c["kind"] == "straight" else np.array(c["polyline_mm"])
        out[c["id"]] = (w(p), c["width_mm"] / 1000)
    return out


def norm_for(metric):
    v = np.array([SEC[c["id"]][metric] for c in EQ["conveyors"]])
    if metric == "utilization":
        return Normalize(0, 1)
    return Normalize(0, float(np.percentile(v, 99)) or 1.0)


def build_usd(polys):
    src = OUT / "scene" / "factory.usda"
    dst = OUT / "scene" / "factory_sim.usda"
    if dst.exists():
        dst.unlink()
    st = Usd.Stage.CreateNew(str(dst))
    st.GetRootLayer().subLayerPaths.append("./factory.usda")
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    st.SetDefaultPrim(st.GetPrimAtPath("/World"))
    base = Usd.Stage.Open(str(src))
    nrm = norm_for(COLOR_METRIC)
    UsdGeom.Scope.Define(st, "/World/SimOverlay")
    elev = section_elev()
    for c in EQ["conveyors"]:
        r = SEC[c["id"]]
        # 컨베이어 prim에 결과 속성 (over)
        path = [p for p in base.Traverse() if p.GetName() == c["id"]][0].GetPath()
        prim = st.OverridePrim(path)
        for k, t in (("throughput_per_h", Sdf.ValueTypeNames.Double), ("avg_wait_s", Sdf.ValueTypeNames.Double),
                     ("utilization", Sdf.ValueTypeNames.Double), ("capacity", Sdf.ValueTypeNames.Int),
                     ("bottleneck", Sdf.ValueTypeNames.Bool), ("congested", Sdf.ValueTypeNames.Bool)):
            prim.CreateAttribute(f"sim:{k}", t, custom=True).Set(r[k])
        # 색 띠
        P, W = polys[c["id"]]
        a, b = ribbon(P, W * 0.85)
        z0, z1 = elev[c["id"]]
        cl = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(P, axis=0).T))])
        zs = 0.82 + z0 + (z1 - z0) * cl / cl[-1]          # 높인 구간·경사로를 따라감
        pts = [Gf.Vec3f(float(x), float(y), float(z)) for (x, y), z in zip(a, zs)] + \
              [Gf.Vec3f(float(x), float(y), float(z)) for (x, y), z in zip(b, zs)]
        n = len(a)
        faces, counts = [], []
        for i in range(n - 1):
            faces += [i, i + 1, n + i + 1, n + i]
            counts.append(4)
        m = UsdGeom.Mesh.Define(st, f"/World/SimOverlay/{c['id']}")
        m.CreatePointsAttr(pts)
        m.CreateFaceVertexCountsAttr(counts)
        m.CreateFaceVertexIndicesAttr(faces)
        m.CreateDoubleSidedAttr(True)
        col = color_of(r, COLOR_METRIC, nrm)
        m.CreateDisplayColorAttr([Gf.Vec3f(*col)])
        m.GetPrim().CreateAttribute("sim:metric", Sdf.ValueTypeNames.String, custom=True).Set(COLOR_METRIC)
        m.GetPrim().CreateAttribute("sim:value", Sdf.ValueTypeNames.Double, custom=True).Set(r[COLOR_METRIC])
        if r["bottleneck"]:
            mid = P[len(P) // 2]
            zb = (z0 + z1) / 2
            for name, size, zc in (("Post", (0.05, 0.05, 1.0), 1.3 + zb), ("Flag", (0.35, 0.35, 0.35), 1.95 + zb)):
                cube = UsdGeom.Cube.Define(st, f"/World/SimOverlay/{c['id']}_bottleneck{name}")
                cube.CreateSizeAttr(1.0)
                api = UsdGeom.XformCommonAPI(cube)
                api.SetTranslate(Gf.Vec3d(float(mid[0]), float(mid[1]), zc))
                api.SetScale(Gf.Vec3f(*size))
                cube.CreateDisplayColorAttr([Gf.Vec3f(*hex2rgb(CRITICAL))])
    # Omniverse 뷰어용: 조명과 시작 시점 (pyrender 렌더에는 영향 없음)
    from pxr import UsdLux
    dome = UsdLux.DomeLight.Define(st, "/World/Lights/Dome")
    dome.CreateIntensityAttr(700.0)
    sun = UsdLux.DistantLight.Define(st, "/World/Lights/Sun")
    sun.CreateIntensityAttr(2500.0)
    sun.CreateAngleAttr(1.5)
    UsdGeom.XformCommonAPI(sun).SetRotate(Gf.Vec3f(40.0, 0.0, 30.0))
    st.GetRootLayer().customLayerData = {
        "cameraSettings": {
            "Perspective": {"position": Gf.Vec3d(31, 44, 12), "target": Gf.Vec3d(13, 56, 0.8)},
            "boundCamera": "/OmniverseKit_Persp",
        }
    }
    st.GetRootLayer().Save()
    print("wrote", dst)
    return dst


def top_render(scene):
    allp = np.vstack([w(c["polyline_mm"]) for c in EQ["conveyors"]])
    x0, y0 = (allp.min(0) - 1.5)
    x1, y1 = (allp.max(0) + 1.5)
    y0 = 45.0   # 인입 라인 아래쪽은 모두 비슷해 루프 영역 위주로
    ppm = 80
    half = ((x1 - x0) / 2, (y1 - y0) / 2)
    size = (int(2 * half[0] * ppm), int(2 * half[1] * ppm))
    img = usd_render.render(scene, eye=((x0 + x1) / 2, (y0 + y1) / 2, 60), target=((x0 + x1) / 2, (y0 + y1) / 2, 0),
                            size=size, ortho=half, shadows=False)
    fig, ax = plt.subplots(figsize=(size[0] / 100, size[1] / 100 + 1.2), facecolor=SURFACE)
    ax.imshow(img, extent=(x0, x1, y0, y1), origin="upper")
    ax.axis("off")
    sm = plt.cm.ScalarMappable(norm=norm_for(COLOR_METRIC), cmap=CMAP)
    cb = fig.colorbar(sm, ax=ax, orientation="horizontal", fraction=0.04, pad=0.02, shrink=0.5)
    cb.set_label("Utilization (mean occupancy / capacity): green = free, red = full", color=INK2)
    cb.outline.set_visible(False)
    bn = ", ".join(i[2:] for i in RES["summary"]["bottlenecks"]) or "none"
    ax.legend(handles=[Patch(fc=CRITICAL, label=f"Bottleneck / congested (forced red). Bottleneck TAG: {bn}")],
              loc="upper right", frameon=True,
              labelcolor=INK)
    ax.set_title("Simulation result on USD scene: utilization (top view, loop & sort lanes)", color=INK, loc="left")
    out = OUT / "s4_top_utilization.png"
    fig.savefig(out, dpi=100, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out)


def metric_maps(polys):
    """루프·레인 영역 2D 지도, 지표 3개 small multiples. 병목은 빨간 외곽 + 범례."""
    x0, x1, y0, y1 = -1.0, 27.0, 46.5, 62.0
    fig, axes = plt.subplots(3, 1, figsize=(13, 22), facecolor=SURFACE)
    for ax, (metric, label) in zip(axes, METRICS):
        nrm = norm_for(metric)
        for cid, (P, W) in polys.items():
            r = SEC[cid]
            a, b = ribbon(P, W)
            ax.add_patch(Polygon(np.vstack([a, b[::-1]]), closed=True, fc=color_of(r, metric, nrm),
                                 ec=SURFACE, lw=1.0, zorder=2))   # 2px 표면색 간격
            if r["bottleneck"]:
                ax.add_patch(Polygon(np.vstack([a, b[::-1]]), closed=True, fill=False, ec=CRITICAL, lw=2.2,
                                     zorder=3))
        sm = plt.cm.ScalarMappable(norm=nrm, cmap=CMAP_SEQ if metric == "throughput_per_h" else CMAP)
        cb = fig.colorbar(sm, ax=ax, fraction=0.025, pad=0.01)
        cb.outline.set_visible(False)
        cb.ax.tick_params(colors=INK2, labelsize=9)
        ax.set_title(label, loc="left", color=INK, fontsize=12)
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
        ax.set_facecolor(SURFACE)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=INK2, labelsize=8)
        ax.set_xlabel("x (m)", color=INK2, fontsize=9)
        ax.set_ylabel("y (m)", color=INK2, fontsize=9)
    # 상위 대기 구간 직접 라벨 (선택적으로 5개만)
    ax = axes[1]
    top = sorted(SEC.values(), key=lambda r: -r["avg_wait_s"])[:4]
    for r in top:
        P, _ = polys[r["id"]]
        m = P[len(P) // 2]
        ax.annotate(f"{r['id'][2:]}  {r['avg_wait_s']:.0f}s", m, xytext=(m[0] + 0.8, m[1] + 0.9), fontsize=8,
                    color=INK, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.6), zorder=5)
    s = RES["summary"]
    fig.suptitle(f"SimPy result ({s['sim_span_s']/3600:.2f} h after warm-up): completed {s['completed_per_h']:.0f}/h "
                 f"of {s['offered_per_h']:.0f}/h offered, lead time {s['avg_lead_time_s']:.0f}s, "
                 f"recirculations {s['recirculations']}", color=INK, fontsize=12, x=0.02, ha="left", y=0.995)
    fig.legend(handles=[Line2D([], [], color=CRITICAL, lw=2.2, label="Bottleneck (outlined)"),
                        Patch(fc=CRITICAL, label="Congested (avg wait >= 5 s)")], loc="upper right",
               frameon=False, labelcolor=INK)
    fig.text(0.02, 0.003, "Infeed lines 11xx/12xx/13xx below y=46.5 m omitted here; all values in out/sim_results.csv",
             color=INK2, fontsize=9)
    fig.tight_layout(rect=(0, 0.01, 1, 0.985))
    out = OUT / "s4_map_metrics.png"
    fig.savefig(out, dpi=110, facecolor=SURFACE)
    plt.close(fig)
    print("wrote", out)


def main():
    polys = section_polys()
    dst = build_usd(polys)
    metric_maps(polys)
    scene, _ = usd_render.build_scene(dst)
    top_render(scene)
    for name, v in VIEWS.items():
        img = usd_render.render(scene, v["eye"], v["target"], size=(1800, 1100), yfov_deg=v["yfov"])
        Image.fromarray(img).save(OUT / f"s4_view_{name}.png")
        print("wrote", OUT / f"s4_view_{name}.png")


if __name__ == "__main__":
    main()
