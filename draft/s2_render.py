"""Step 2 확인 이미지.
  - out/s2_top_overlay.png : 위에서 본 직교 렌더 + 원본 도면 선(빨강) 겹침 → 배치가 도면과 맞는지
  - out/s2_view_overview.png, s2_view_sortlanes.png, s2_view_merge.png : 비스듬한 3D 렌더

사용: python s2_render.py [scene.usda] [출력 접두어]
"""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
sys.path.insert(0, str(HERE))
import usd_render  # noqa: E402
from s1_plot_plan import dxf_paths  # noqa: E402

EQ = json.load(open(OUT / "equipment.json"))
ORIGIN = np.array(EQ["origin_mm"])


def world_xy(mm):
    return (np.asarray(mm) - ORIGIN) / 1000.0


# 3D 시점 (월드 m). 도면 원점 보정 후 장면은 약 x 0~26, y 0~61
VIEWS = {
    "overview": dict(eye=(30, -6, 34), target=(12, 34, 0), yfov=55),
    "sortlanes": dict(eye=(30, 40, 9), target=(13, 53.5, 0.5), yfov=45),
    "crossing": dict(eye=(31, 47.5, 9.5), target=(14, 56.8, 0.8), yfov=42),   # 입체 교차(16xx 레인이 상단 루프 위로)
}


def top_region(scene, out, x0, x1, y0, y1, px_per_m, title, lw=0.6):
    """[x0,x1]x[y0,y1] (월드 m) 영역을 위에서 직교 렌더 + 원본 도면 선(빨강) 겹침."""
    ctr = ((x0 + x1) / 2, (y0 + y1) / 2)
    half = ((x1 - x0) / 2, (y1 - y0) / 2)
    size = (int(2 * half[0] * px_per_m), int(2 * half[1] * px_per_m))
    img = usd_render.render(scene, eye=(ctr[0], ctr[1], 60), target=(ctr[0], ctr[1], 0), size=size,
                            ortho=half, shadows=False)
    fig = plt.figure(figsize=(size[0] / 100, size[1] / 100 + 0.4))
    ax = fig.add_axes([0, 0, 1, size[1] / (size[1] + 40)])
    ax.imshow(img, extent=(x0, x1, y0, y1), origin="upper")  # 직교 카메라 up=+Y → 화면 위 = +Y
    ax.add_collection(LineCollection([world_xy(p) for p in dxf_paths()], colors="#e0102a",
                                     linewidths=lw, alpha=0.9))
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    ax.axis("off")
    fig.suptitle(title, fontsize=10, y=0.998)
    fig.savefig(out, dpi=100, facecolor="white")
    plt.close(fig)
    print("wrote", out)


def top_overlay(scene, prefix, title):
    allp = np.vstack([world_xy(c["polyline_mm"]) for c in EQ["conveyors"]])
    mn, mx = allp.min(0) - 1.5, allp.max(0) + 1.5
    top_region(scene, OUT / f"{prefix}_top_overlay.png", mn[0], mx[0], mn[1], mx[1], 40, title, lw=0.4)
    top_region(scene, OUT / f"{prefix}_top_overlay_loop.png", -1, 27, 47, 62, 90,
               title + " | loop & sort lanes")
    top_region(scene, OUT / f"{prefix}_top_overlay_merge.png", 2, 9.5, 47.5, 52.5, 220,
               title + " | 11xx merge")


def main():
    scene_path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "scene" / "factory.usda"
    prefix = sys.argv[2] if len(sys.argv) > 2 else "s2"
    scene, stats = usd_render.build_scene(scene_path)
    for k, (n, tris) in stats.items():
        print(f"  prototype {k}: {n} instances x {tris//3 if tris > 1e6 else tris} tris/instance")
    top_overlay(scene, prefix, f"{scene_path.name}: top-down orthographic render + DXF lines (red)")
    for name, v in VIEWS.items():
        img = usd_render.render(scene, v["eye"], v["target"], size=(1800, 1100), yfov_deg=v["yfov"])
        Image.fromarray(img).save(OUT / f"{prefix}_view_{name}.png")
        print("wrote", OUT / f"{prefix}_view_{name}.png")


if __name__ == "__main__":
    main()
