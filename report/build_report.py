"""report/template.html → report/index.html (그림을 파일 안에 넣은 한 파일짜리 HTML) → report/cad_twin_report.pdf

- 그림: template의 src="images/NAME.png"를 draft/out/NAME.png의 base64 data URI로 바꿈
  (VS Code HTML Preview처럼 상대 경로 이미지를 못 읽는 뷰어에서도 보이게)
- 출구별 처리량 그래프와 표: draft/out/sim_results.json에서 읽어 정적 SVG로 그림 (스크립트가 막힌 뷰어에서도 보이게)
- 마지막에 make_pdf.py로 PDF 생성 (--no-pdf로 생략 가능)

사용: python3 report/build_report.py [--no-pdf]
"""
import base64
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = ROOT / "draft" / "out"
TEMPLATE = HERE / "template.html"
TARGET = HERE / "index.html"


def embed_images(html):
    missing = []

    def repl(m):
        path = OUT_DIR / m.group(1)
        if not path.exists():
            missing.append(m.group(1))
            return m.group(0)
        data = base64.b64encode(path.read_bytes()).decode()
        return f'src="data:image/png;base64,{data}"'

    html = re.sub(r'src="images/([\w.-]+\.png)"', repl, html)
    if missing:
        raise SystemExit(f"draft/out에 없는 그림: {missing} (draft 스크립트를 먼저 실행)")
    return html


def sink_chart(results):
    sinks = sorted((s for s in results["sections"] if s["role"] == "sink"), key=lambda s: s["id"])
    hot = set(results["summary"]["bottlenecks"])
    service_s = results["config"]["sinks"]["default"]["service_s"]
    limit = 3600 / service_s

    W, H, L, R, T, B = 720, 270, 44, 10, 18, 40
    pw, ph = W - L - R, H - T - B
    top_v = max(1000, int((max(s["throughput_per_h"] for s in sinks) // 250 + 1) * 250))

    def y(v):
        return T + ph - v / top_v * ph

    hot_txt = ", ".join(sorted(hot)) or "없음"
    others = [s["throughput_per_h"] for s in sinks if s["id"] not in hot]
    aria = (f"출구별 처리량 막대 그래프. 병목 {hot_txt}, 나머지 {len(others)}곳은 시간당 "
            f"{min(others):.0f}에서 {max(others):.0f}개 사이")
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{aria}" xmlns="http://www.w3.org/2000/svg">']
    for v in range(0, top_v + 1, 250):
        cls = "c-base" if v == 0 else "c-grid"
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="{cls}"/>')
        out.append(f'<text x="{L - 8}" y="{y(v) + 3.5:.1f}" text-anchor="end" class="c-tick">{v:,}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(limit):.1f}" y2="{y(limit):.1f}" class="c-limit"/>')
    out.append(f'<text x="{W - R}" y="{y(limit) - 6:.1f}" text-anchor="end" class="c-label">출구 최대량 {limit:,.0f}</text>')

    step = pw / len(sinks)
    bw = min(18, step * 0.5)
    for i, s in enumerate(sinks):
        v = s["throughput_per_h"]
        cx = L + step * i + step / 2
        top, base = y(v), y(0)
        r = min(4, (base - top) / 2)
        x0, x1 = cx - bw / 2, cx + bw / 2
        cls = "c-bar hot" if s["id"] in hot else "c-bar"
        d = (f"M{x0:.1f},{base:.1f} L{x0:.1f},{top + r:.1f} Q{x0:.1f},{top:.1f} {x0 + r:.1f},{top:.1f} "
             f"L{x1 - r:.1f},{top:.1f} Q{x1:.1f},{top:.1f} {x1:.1f},{top + r:.1f} L{x1:.1f},{base:.1f} Z")
        out.append(f'<g class="c-g"><title>{s["id"]} · 시간당 {v:,.1f}개 · 점유율 {s["utilization"]:.3f}</title>'
                   f'<rect x="{L + step * i:.1f}" y="{T}" width="{step:.1f}" height="{ph}" class="c-hit"/>'
                   f'<path d="{d}" class="{cls}"/></g>')
        out.append(f'<text x="{cx:.1f}" y="{H - B + 16}" text-anchor="middle" class="c-tick">{s["id"][2:]}</text>')
        if s["id"] in hot:
            out.append(f'<text x="{x1 + 6:.1f}" y="{top + 12:.1f}" class="c-value">{v:,.0f}</text>')
    lane = "·".join(h[2:] for h in sorted(hot))
    out.append(f'<text x="{L}" y="{H - 6}" class="c-label">출구(이름표 번호). 병목 출구 {lane}만 빨강</text>')
    out.append("</svg>")

    rows = "".join(
        f'<tr><td class="mono">{s["id"]}</td><td class="num">{s["throughput_per_h"]:.1f}</td>'
        f'<td class="num">{s["utilization"]:.3f}</td></tr>'
        for s in sinks)
    return "\n".join(out), rows


def main():
    html = TEMPLATE.read_text(encoding="utf-8")
    results = json.loads((OUT_DIR / "sim_results.json").read_text(encoding="utf-8"))
    chart, rows = sink_chart(results)
    for key, val in (("<!--SINK_CHART-->", chart), ("<!--SINK_ROWS-->", rows)):
        if key not in html:
            raise SystemExit(f"template에 {key} 자리가 없음")
        html = html.replace(key, val)
    html = embed_images(html)
    TARGET.write_text(html, encoding="utf-8")
    print(f"wrote {TARGET} ({TARGET.stat().st_size / 1e6:.1f} MB)")

    if "--no-pdf" not in sys.argv:
        sys.path.insert(0, str(HERE))
        import make_pdf
        make_pdf.main()


if __name__ == "__main__":
    main()
