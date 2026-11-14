"""Step 1 확인 이미지: 원본 도면(회색) 위에 추출한 설비(컨베이어 외곽·흐름·연결, 제어반, IoT)를 겹쳐 그림.

출력: out/s1_plan_overall.png, out/s1_plan_zoom_loop.png, out/s1_plan_zoom_infeed.png
"""
import json
from pathlib import Path

import ezdxf
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Polygon

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
EQ = json.load(open(OUT / "equipment.json"))
ROOT = HERE.parent

LINE_COLORS = {"11xx": "#1f77b4", "12xx": "#2ca02c", "13xx": "#9467bd",
               "14xx": "#ff7f0e", "15xx": "#17becf", "16xx": "#8c564b"}
LINK_COLORS = {"merge": "#d62728", "divert": "#e377c2", "side_transfer": "#2b2b2b"}


def offset_outline(poly, w):
    """중심선 polyline → 폭 w 외곽 다각형."""
    p = np.asarray(poly, float)
    d = np.gradient(p, axis=0)
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    n = np.stack([-d[:, 1], d[:, 0]], 1)
    return np.vstack([p + n * w / 2, (p - n * w / 2)[::-1]])


_DXF_CACHE = None


def dxf_paths():
    """원본 도면의 모든 선/호/폴리라인/원을 블록까지 풀어서 WCS 좌표 polyline 목록으로."""
    global _DXF_CACHE
    if _DXF_CACHE is None:
        from ezdxf import disassemble, path as dpath
        doc = ezdxf.readfile(ROOT / EQ["source"])
        polys = []
        for e in disassemble.recursive_decompose(doc.modelspace()):
            if e.dxftype() in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"):
                pts = np.array([(v.x, v.y) for v in dpath.make_path(e).flattening(20)])
                if len(pts) >= 2:
                    polys.append(pts)
        _DXF_CACHE = polys
    return _DXF_CACHE


def draw_dxf(ax):
    from matplotlib.collections import LineCollection
    ax.add_collection(LineCollection(dxf_paths(), colors="0.45", linewidths=0.4, zorder=1))


def draw_overlay(ax, label_tags=True, fs=4):
    for c in EQ["conveyors"]:
        col = LINE_COLORS.get(c["line"], "k")
        ax.add_patch(Polygon(offset_outline(c["polyline_mm"], c["width_mm"]), closed=True,
                             fc=col, ec=col, alpha=0.35, lw=0.6, zorder=5))
        p = np.array(c["polyline_mm"])
        # 흐름 화살표 (구간 중앙)
        m = len(p) // 2
        a, b = (p[m - 1], p[m]) if len(p) > 2 else (p[0], p[1])
        d = (b - a) / (np.linalg.norm(b - a) + 1e-9)
        mid = np.array(c["center_mm"])
        ax.add_patch(FancyArrowPatch(mid - d * 250, mid + d * 250, arrowstyle="-|>",
                                     mutation_scale=6, color=col, lw=0.8, zorder=7))
        if label_tags:
            nrm = np.array([-d[1], d[0]])
            q = mid + nrm * (c["width_mm"] / 2 + 250)
            ax.text(q[0], q[1], str(c["tag"]), fontsize=fs, ha="center", va="center",
                    color=col, zorder=8)
    byid = {c["id"]: c for c in EQ["conveyors"]}
    for l in EQ["links"]:
        if l["type"] == "inline":
            continue
        s, d = byid[l["src"]], byid[l["dst"]]
        ax.add_patch(FancyArrowPatch(s["center_mm"], d["center_mm"], arrowstyle="-|>",
                                     mutation_scale=10, color=LINK_COLORS[l["type"]],
                                     lw=1.6, zorder=9, connectionstyle="arc3,rad=0.25"))
    for cp in EQ["control_panels"]:
        ax.add_patch(Polygon(cp["outline_mm"], closed=True, fc="#d62728", ec="k", alpha=0.7,
                             lw=0.5, zorder=6))
        ctr = cp["center_mm"]
        ax.text(ctr[0], ctr[1], cp["id"], fontsize=fs + 1, ha="center", va="center",
                color="white", weight="bold", zorder=8)
    iot = np.array([d["pos_mm"] for d in EQ["iot"]])
    ax.scatter(iot[:, 0], iot[:, 1], s=4, c="#ffbf00", edgecolors="k", linewidths=0.3, zorder=8)


def legend(ax):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    h = [Patch(fc=c, alpha=0.5, label=f"conveyor {k}") for k, c in LINE_COLORS.items()]
    h += [Line2D([], [], color=LINK_COLORS["merge"], lw=2, label="merge"),
          Line2D([], [], color=LINK_COLORS["divert"], lw=2, label="divert"),
          Line2D([], [], color=LINK_COLORS["side_transfer"], lw=2, label="side transfer"),
          Patch(fc="#d62728", alpha=0.7, label="control panel"),
          Line2D([], [], marker="o", ls="", mfc="#ffbf00", mec="k", label="IoT")]
    ax.legend(handles=h, loc="upper right", fontsize=7, framealpha=0.9)


def render(name, xlim, ylim, width_in, label_tags, fs, title):
    fig = plt.figure(figsize=(width_in, width_in * (ylim[1] - ylim[0]) / (xlim[1] - xlim[0]) + 0.4))
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.94])
    draw_dxf(ax)
    draw_overlay(ax, label_tags, fs)
    legend(ax)
    ax.set_aspect("equal", adjustable="box"); ax.set_xlim(*xlim); ax.set_ylim(*ylim)
    ax.set_title(title, fontsize=9)
    fig.savefig(OUT / name, dpi=200, facecolor="white")
    plt.close(fig)
    print("wrote", OUT / name)


def main():
    allp = np.vstack([np.array(c["polyline_mm"]) for c in EQ["conveyors"]])
    mn, mx = allp.min(0) - 1500, allp.max(0) + 1500
    w = (mx[0] - mn[0]) / 1000
    h = (mx[1] - mn[1]) / 1000
    render("s1_plan_overall.png", (mn[0], mx[0]), (mn[1], mx[1]), 8, False, 3,
           f"Extracted equipment over DXF  ({w:.1f} m x {h:.1f} m)  "
           f"conveyors={len(EQ['conveyors'])} CP={len(EQ['control_panels'])} IoT={len(EQ['iot'])}")
    render("s1_plan_zoom_loop.png", (121500, 148500), (79500, 93000), 16, True, 4.5,
           "Zoom: main line 14xx, recirculation loop 15xx, sort lanes 16xx")
    render("s1_plan_zoom_infeed.png", (126000, 149000), (31000, 45000), 12, True, 5,
           "Zoom: infeed lines 11xx / 12xx / 13xx (bottom end)")


if __name__ == "__main__":
    main()
