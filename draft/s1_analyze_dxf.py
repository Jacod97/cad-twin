"""Step 1: DXF 배치도 분석 → 설비 목록 JSON.

도면 구조 (분석 결과):
  - 레이어 0 LINE/ARC = 컨베이어 중심선 (폭 정보 없음)
  - 중심선에 직교하는 200mm 짧은 선 = 구간 경계 틱
    (*U28/*U30/*U32 블록은 세로 인입 라인 3개의 틱 23개씩을 묶은 것: *U27/29/31 = 200mm 선 1개)
  - 블록 "name" (TAGNUMBER) = 구간 중앙에 놓인 이름표
  - 블록 "IOT" = 구간마다 1개씩 있는 센서 점
  - 블록 "CP..." = 제어반 외곽(사각형) + 문 열림 호. MTEXT "CPn" = 제어반 이름

출력: draft/out/equipment.json, 콘솔 요약
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import ezdxf
import ezdxf.path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DXF = ROOT / "data/dxf/designscad_conveyor-system-sorting-lane-plan-with-control-poi_1301.dxf"
OUT = Path(__file__).resolve().parent / "out"
CFG_PATH = Path(__file__).resolve().parent / "config.yaml"

TICK_LEN = 200.0      # 구간 경계 틱 길이 (mm)
SNAP = 2.0            # 끝점 일치 허용오차 (mm)
GAP_REPAIR = 150.0    # 끊긴 끝점끼리 이어 붙일 최대 거리 (mm) - 도면 작도 오차 보정
ARC_STEP_DEG = 1.0    # 호를 폴리라인으로 근사할 각도 간격


def load_cfg():
    import yaml
    with open(CFG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- geometry
def arc_points(c, r, a0, a1):
    # 중심선 ARC는 모두 extrusion +Z (확인함). 그대로 각도 사용
    a0, a1 = math.radians(a0), math.radians(a1)
    if a1 <= a0:
        a1 += 2 * math.pi
    n = max(2, int(math.ceil(math.degrees(a1 - a0) / ARC_STEP_DEG)) + 1)
    t = np.linspace(a0, a1, n)
    return np.stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)], 1)


def project(poly, p):
    """polyline 위 최근접점: (거리, 호길이 위치 s, 점)."""
    best = (1e18, 0.0, None)
    acc = 0.0
    for a, b in zip(poly[:-1], poly[1:]):
        d = b - a
        L = float(np.hypot(*d))
        t = 0.0 if L == 0 else float(np.clip(np.dot(p - a, d) / L**2, 0, 1))
        q = a + t * d
        dist = float(np.hypot(*(p - q)))
        if dist < best[0]:
            best = (dist, acc + t * L, q)
        acc += L
    return best


def cumlen(poly):
    return np.concatenate([[0], np.cumsum(np.hypot(*np.diff(poly, axis=0).T))])


def sub_poly(poly, s0, s1):
    """polyline의 호길이 [s0, s1] 부분."""
    cl = cumlen(poly)
    pts = [np.interp(s0, cl, poly[:, 0]), np.interp(s0, cl, poly[:, 1])]
    out = [pts]
    for i, s in enumerate(cl):
        if s0 < s < s1:
            out.append(list(poly[i]))
    out.append([np.interp(s1, cl, poly[:, 0]), np.interp(s1, cl, poly[:, 1])])
    return np.array(out)


def tangent_at(poly, s):
    cl = cumlen(poly)
    i = int(np.clip(np.searchsorted(cl, s) - 1, 0, len(poly) - 2))
    # 길이 0 조각은 건너뛰고 가장 가까운 유효 조각 방향 사용
    for k in sorted(range(len(poly) - 1), key=lambda k: abs(k - i)):
        d = poly[k + 1] - poly[k]
        n = np.linalg.norm(d)
        if n > 1e-6:
            return d / n
    raise ValueError("degenerate polyline")


# ---------------------------------------------------------------- read DXF
def read_dxf():
    doc = ezdxf.readfile(DXF)
    msp = doc.modelspace()

    prims = []   # 중심선 조각: dict(kind, poly)
    ticks = []   # 틱 (중점, 방향)

    def add_line(a, b):
        a, b = np.array([a[0], a[1]]), np.array([b[0], b[1]])
        L = np.hypot(*(b - a))
        if abs(L - TICK_LEN) < 1.0:
            ticks.append(((a + b) / 2, (b - a) / L))
        else:
            prims.append(dict(kind="line", poly=np.array([a, b])))

    for e in msp.query('LINE[layer=="0"]'):
        add_line(e.dxf.start, e.dxf.end)
    for e in msp.query('ARC[layer=="0"]'):
        prims.append(dict(kind="arc", poly=arc_points(e.dxf.center, e.dxf.radius,
                                                      e.dxf.start_angle, e.dxf.end_angle),
                          center=list(e.dxf.center)[:2], radius=e.dxf.radius))
    # *U 블록: 틱 묶음
    for ins in msp.query('INSERT[layer=="0"]'):
        if ins.dxf.name.startswith("*U"):
            for v in ins.virtual_entities():          # 중첩 INSERT
                for s in (v.virtual_entities() if v.dxftype() == "INSERT" else [v]):
                    if s.dxftype() == "LINE":
                        add_line(s.dxf.start, s.dxf.end)

    tags = []
    for e in msp.query('INSERT[name=="name"]'):
        attrs = {a.dxf.tag: a.dxf.text for a in e.attribs}
        tags.append(dict(tag=int(attrs["TAGNUMBER"]), pos=np.array(list(e.dxf.insert)[:2])))

    iots = []
    for e in msp.query('INSERT[name=="IOT"]'):
        for v in e.virtual_entities():
            if v.dxftype() == "CIRCLE":
                c = v.ocs().to_wcs(v.dxf.center)
                iots.append(dict(pos=np.array([c.x, c.y]), radius=v.dxf.radius,
                                 handle=e.dxf.handle))

    cps = []
    for e in msp.query("INSERT"):
        n = e.dxf.name
        if not n.lower().startswith("cp"):
            continue
        rect, door = None, []
        for v in e.virtual_entities():
            # 미러(xscale=-1) 블록은 OCS 법선이 -Z → 반드시 WCS로 변환
            if v.dxftype() == "LWPOLYLINE":
                rect = np.array([[p.x, p.y] for p in v.vertices_in_wcs()])
            elif v.dxftype() == "ARC":
                c = v.ocs().to_wcs(v.dxf.center)
                pts = np.array([[p.x, p.y] for p in ezdxf.path.make_path(v).flattening(5)])
                door.append(dict(center=[c.x, c.y], radius=v.dxf.radius, poly=pts.tolist()))
        cps.append(dict(block=n, layer=e.dxf.layer, rect=rect, door=door, handle=e.dxf.handle))
    texts = []
    for e in msp.query('MTEXT[layer=="CP"]'):
        texts.append(dict(text=e.plain_text().strip(), pos=np.array(list(e.dxf.insert)[:2])))
    return prims, ticks, tags, iots, cps, texts


# ---------------------------------------------------------------- network
def build_chains(prims, ticks, log):
    """중심선 조각을 노드/엣지 그래프로 만들고, 틱 위치에서 자른 '구간' 목록을 만든다."""
    # 1) 끝점 노드
    nodes = []          # 좌표
    def node_id(p):
        for i, q in enumerate(nodes):
            if np.hypot(*(p - q)) < SNAP:
                return i
        nodes.append(p.copy())
        return len(nodes) - 1

    edges = []  # dict(a, b, poly, kind)
    for pr in prims:
        poly = pr["poly"]
        edges.append(dict(a=node_id(poly[0]), b=node_id(poly[-1]), poly=poly, kind=pr["kind"]))

    # 2) T 접합: 끝점이 다른 조각의 내부에 놓이면 그 조각을 쪼갬
    changed = True
    while changed:
        changed = False
        deg = defaultdict(int)
        for e in edges:
            deg[e["a"]] += 1
            deg[e["b"]] += 1
        for nid, p in enumerate(nodes):
            for ei, e in enumerate(edges):
                if nid in (e["a"], e["b"]):
                    continue
                d, s, _ = project(e["poly"], p)
                L = cumlen(e["poly"])[-1]
                if d < SNAP and SNAP < s < L - SNAP:
                    e1 = dict(a=e["a"], b=nid, poly=sub_poly(e["poly"], 0, s), kind=e["kind"])
                    e2 = dict(a=nid, b=e["b"], poly=sub_poly(e["poly"], s, L), kind=e["kind"])
                    edges[ei:ei + 1] = [e1, e2]
                    log.append(f"T-junction at ({p[0]:.0f},{p[1]:.0f})")
                    changed = True
                    break
            if changed:
                break

    # 3) 끊긴 끝점 보정 (작도 오차)
    deg = defaultdict(int)
    for e in edges:
        deg[e["a"]] += 1
        deg[e["b"]] += 1
    ends = [n for n in range(len(nodes)) if deg[n] == 1]
    merged = {}
    for i in ends:
        for j in ends:
            if i < j and i not in merged and j not in merged:
                g = np.hypot(*(nodes[i] - nodes[j]))
                if g < GAP_REPAIR:
                    merged[j] = i
                    log.append(f"GAP repaired: ({nodes[i][0]:.0f},{nodes[i][1]:.0f})"
                               f" <-> ({nodes[j][0]:.0f},{nodes[j][1]:.0f}) gap={g:.0f}mm")
    for e in edges:
        e["a"] = merged.get(e["a"], e["a"])
        e["b"] = merged.get(e["b"], e["b"])
    # 보정된 엣지의 끝점을 노드 좌표로 이어줌
    for e in edges:
        p = e["poly"].copy()
        na, nb = nodes[e["a"]], nodes[e["b"]]
        # 가까우면 끝점을 노드 좌표로 교체, 멀면(틈 보정) 노드까지 선을 이어 붙임
        if np.hypot(*(p[0] - na)) < SNAP:
            p[0] = na
        else:
            p = np.vstack([na, p])
        if np.hypot(*(p[-1] - nb)) < SNAP:
            p[-1] = nb
        else:
            p = np.vstack([p, nb])
        e["poly"] = p

    # 4) 틱을 엣지 위 절단점으로
    cuts = defaultdict(list)  # edge idx -> [s]
    tick_unmatched = 0
    for c, d in ticks:
        best = None
        for ei, e in enumerate(edges):
            dist, s, _ = project(e["poly"], c)
            if dist < SNAP * 5 and (best is None or dist < best[0]):
                best = (dist, ei, s)
        if best is None:
            tick_unmatched += 1
            continue
        cuts[best[1]].append(best[2])
    if tick_unmatched:
        log.append(f"WARNING: {tick_unmatched} ticks not on any centerline")

    # 5) 노드 차수 / 관통 쌍 (차수 3 노드에서 가장 일직선인 두 엣지를 이어 봄)
    inc = defaultdict(list)  # node -> [(edge, end('a'|'b'))]
    for ei, e in enumerate(edges):
        inc[e["a"]].append((ei, "a"))
        inc[e["b"]].append((ei, "b"))

    def out_dir(ei, end):
        p = edges[ei]["poly"]
        return (p[1] - p[0]) if end == "a" else (p[-2] - p[-1])

    through = {}   # node -> set of 2 edge-ends that continue
    junctions = {}
    for n, lst in inc.items():
        if len(lst) == 2:
            through[n] = lst
        elif len(lst) >= 3:
            best = None
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    u = out_dir(*lst[i]); v = out_dir(*lst[j])
                    cos = np.dot(u, v) / np.linalg.norm(u) / np.linalg.norm(v)
                    if best is None or cos < best[0]:
                        best = (cos, i, j)
            through[n] = [lst[best[1]], lst[best[2]]]
            junctions[n] = [x for k, x in enumerate(lst) if k not in (best[1], best[2])]

    # 6) 체인: 관통 연결로 엣지를 이어 하나의 긴 polyline으로
    used = set()
    chains = []
    for ei in range(len(edges)):
        if ei in used:
            continue
        # 시작 엣지에서 양방향으로 확장
        seq = [(ei, True)]  # (edge, forward)
        used.add(ei)
        for direction in (1, -1):
            cur_e, cur_fwd = seq[-1] if direction == 1 else seq[0]
            while True:
                end_node = edges[cur_e]["b" if cur_fwd == (direction == 1) else "a"]
                pair = through.get(end_node)
                if not pair:
                    break
                nxt = [x for x in pair if x[0] != cur_e]
                if not nxt or nxt[0][0] in used:
                    break
                ne, nend = nxt[0]
                used.add(ne)
                fwd = (nend == "a") if direction == 1 else (nend == "b")
                if direction == 1:
                    seq.append((ne, fwd))
                else:
                    seq.insert(0, (ne, fwd))
                cur_e, cur_fwd = ne, fwd
        # polyline 합치기 + 절단점/노드 위치 기록
        poly, cut_s, node_s, acc = [], [], [], 0.0
        for k, (e_i, fwd) in enumerate(seq):
            p = edges[e_i]["poly"] if fwd else edges[e_i]["poly"][::-1]
            L = cumlen(p)[-1]
            start_node = edges[e_i]["a"] if fwd else edges[e_i]["b"]
            end_node = edges[e_i]["b"] if fwd else edges[e_i]["a"]
            if k == 0:
                node_s.append((start_node, 0.0))
            cut_s += [acc + (s if fwd else L - s) for s in cuts.get(e_i, [])]
            poly += list(p if k == 0 else p[1:])
            acc += L
            node_s.append((end_node, acc))
        chains.append(dict(poly=np.array(poly), cuts=sorted(cut_s), nodes=node_s,
                           edges=[x[0] for x in seq]))
    return nodes, edges, chains, junctions, inc


def make_sections(chains, junctions, log):
    """체인을 틱/끝점에서 잘라 구간 목록 생성. 관통하지 않는 분기 끝은 분기점으로 기록."""
    sections = []
    for ci, ch in enumerate(chains):
        L = cumlen(ch["poly"])[-1]
        bounds = [0.0] + [s for s in ch["cuts"] if 1.0 < s < L - 1.0] + [L]
        for k in range(len(bounds) - 1):
            s0, s1 = bounds[k], bounds[k + 1]
            if s1 - s0 < 1.0:
                continue
            sections.append(dict(chain=ci, s0=s0, s1=s1, poly=sub_poly(ch["poly"], s0, s1)))
    return sections


def assign_tags(sections, tags, log):
    by_sec = defaultdict(list)
    for t in tags:
        best = min(((project(sec["poly"], t["pos"])[0], i) for i, sec in enumerate(sections)))
        by_sec[best[1]].append((best[0], t["tag"]))
        if best[0] > 300:
            log.append(f"WARNING: tag {t['tag']} is {best[0]:.0f}mm from nearest section")
    for i, sec in enumerate(sections):
        lst = sorted(by_sec.get(i, []))
        sec["tag"] = lst[0][1] if lst else None
        if len(lst) > 1:
            log.append(f"WARNING: section {i} has multiple tags {[x[1] for x in lst]}")
        if not lst:
            L = sec["s1"] - sec["s0"]
            log.append(f"NOTE: untagged section len={L:.0f}mm at "
                       f"({sec['poly'][len(sec['poly'])//2][0]:.0f},{sec['poly'][len(sec['poly'])//2][1]:.0f})")


def absorb_untagged(sections, log):
    """태그 없는 짧은 조각(레인 시작부 240mm 이송부, 100mm 작도 오차 꺾임)을 같은 체인의 이웃 구간에 합침.
    체인 시작부 조각은 다음 구간에, 나머지는 앞 구간에 붙인다."""
    by_chain = defaultdict(list)
    for s in sections:
        by_chain[s["chain"]].append(s)
    out = []
    for ci, lst in by_chain.items():
        lst.sort(key=lambda s: s["s0"])
        merged = []
        pending = None
        for s in lst:
            if s["tag"] is None:
                if merged:
                    prev = merged[-1]
                    prev["s1"] = s["s1"]
                    prev["poly"] = np.vstack([prev["poly"], s["poly"][1:]])
                    log.append(f"absorbed {s['s1']-s['s0']:.0f}mm untagged piece into CV{prev['tag']} (end)")
                else:
                    pending = s
                continue
            if pending is not None:
                s["s0"] = pending["s0"]
                s["poly"] = np.vstack([pending["poly"], s["poly"][1:]])
                log.append(f"absorbed {pending['s1']-pending['s0']:.0f}mm untagged piece into CV{s['tag']} (start)")
                pending = None
            merged.append(s)
        out += merged
    return out


def connectivity(sections, chains, nodes, junctions, log):
    """구간 인접관계와 흐름 방향. 방향 규칙: 같은 라인에서 TAGNUMBER가 증가하는 쪽이 하류.
    (1101→1124, 1401→1417, 1501→1537 ... 도면 번호 체계로부터 추정)"""
    # chain 내부 인접 (s1 == 다음 s0)
    by_chain = defaultdict(list)
    for i, s in enumerate(sections):
        by_chain[s["chain"]].append(i)
    adj = []  # (i, end_i, j, end_j, kind)  end: 0=s0쪽, 1=s1쪽
    for ci, lst in by_chain.items():
        lst.sort(key=lambda i: sections[i]["s0"])
        for a, b in zip(lst[:-1], lst[1:]):
            adj.append((a, 1, b, 0, "inline"))
    # chain 끝 ↔ 다른 chain: 관통 안 된 끝(분기)이 붙는 노드를 찾아 그 노드를 지나는 구간과 연결
    def sec_at(ci, s):
        for i in by_chain[ci]:
            if sections[i]["s0"] - 1e-6 <= s <= sections[i]["s1"] + 1e-6:
                return i
    node_pos_in_chain = defaultdict(list)  # node -> [(chain, s)]
    for ci, ch in enumerate(chains):
        for n, s in ch["nodes"]:
            node_pos_in_chain[n].append((ci, s))
    for ci, ch in enumerate(chains):
        L = cumlen(ch["poly"])[-1]
        for n, s in (ch["nodes"][0], ch["nodes"][-1]):
            end = 0 if s == 0 else 1
            others = [(cj, sj) for cj, sj in node_pos_in_chain[n]
                      if not (cj == ci and abs(sj - s) < 1.0)]
            for cj, sj in others:
                i = sec_at(ci, s)
                j = sec_at(cj, sj)
                adj.append((i, end, j, None, "junction"))
    # 방향: 구간마다 흐름이 s0→s1 (+1) 인지 s1→s0 (-1) 인지
    tag_of = {i: s["tag"] for i, s in enumerate(sections)}
    flow = {}
    for i, ei, j, ej, kind in adj:
        if kind != "inline":
            continue
        ti, tj = tag_of[i], tag_of[j]
        if ti is None or tj is None:
            continue
        # i의 s1쪽이 j의 s0쪽과 붙음
        if tj > ti:
            flow[i] = flow.get(i, +1); flow[j] = flow.get(j, +1)
        else:
            flow[i] = flow.get(i, -1); flow[j] = flow.get(j, -1)
    # 태그 없는/고립 구간은 체인 내 이웃 방향을 따름
    for ci, lst in by_chain.items():
        dirs = [flow[i] for i in lst if i in flow]
        for i in lst:
            if i not in flow:
                flow[i] = dirs[0] if dirs else +1
                if not dirs:
                    log.append(f"WARNING: no flow info for section tag={tag_of[i]}, default s0->s1")
    # 링크: inline은 방향대로, junction은 분기 체인 끝의 흐름으로 merge/divert 판정
    links = []
    for i, ei, j, ej, kind in adj:
        if kind == "inline":
            a, b = (i, j) if flow[i] == +1 else (j, i)
            links.append(dict(src=a, dst=b, type="inline"))
        else:
            # i = 분기 체인의 끝 구간, end = 0(s0쪽) / 1(s1쪽)
            exits_here = (ei == 1 and flow[i] == +1) or (ei == 0 and flow[i] == -1)
            if exits_here:
                links.append(dict(src=i, dst=j, type="merge"))
            else:
                links.append(dict(src=j, dst=i, type="divert"))
    for s_i, s in enumerate(sections):
        s["flow"] = flow[s_i]
    return links


# ---------------------------------------------------------------- main
def main():
    cfg = load_cfg()
    widths = cfg["conveyor"]["width_mm"]   # 라인별 폭 {"11xx": 800, ..., "default": 800}
    OUT.mkdir(exist_ok=True)
    log = []
    prims, ticks, tags, iots, cps, texts = read_dxf()
    print(f"centerline prims={len(prims)} (lines={sum(p['kind']=='line' for p in prims)}, "
          f"arcs={sum(p['kind']=='arc' for p in prims)}), ticks={len(ticks)}, tags={len(tags)}, "
          f"iot={len(iots)}, cp blocks={len(cps)}, cp texts={len(texts)}")

    nodes, edges, chains, junctions, inc = build_chains(prims, ticks, log)
    sections = make_sections(chains, junctions, log)
    assign_tags(sections, tags, log)
    sections = absorb_untagged(sections, log)
    links = connectivity(sections, chains, nodes, junctions, log)

    # 구간 → JSON
    conv = []
    for i, s in enumerate(sections):
        poly = s["poly"] if s["flow"] == +1 else s["poly"][::-1]   # 흐름 방향으로 정렬
        L = float(cumlen(poly)[-1])
        chord = poly[-1] - poly[0]
        mid_s = L / 2
        cl = cumlen(poly)
        mid = [float(np.interp(mid_s, cl, poly[:, 0])), float(np.interp(mid_s, cl, poly[:, 1]))]
        tang = tangent_at(poly, mid_s)
        # 회전각: 첫/마지막 '긴' 조각(>=120mm) 방향 차이. 100mm 꺾임(작도 오차)은 무시됨.
        # 호는 1° 간격(약 18mm)으로 근사되므로 순수 호 구간은 첫/마지막 조각을 그대로 씀
        seg = np.diff(poly, axis=0)
        seg_len = np.hypot(*seg.T)
        longs = np.where(seg_len >= 120)[0]
        valid = np.where(seg_len > 1e-6)[0]
        i0, i1 = (longs[0], longs[-1]) if len(longs) else (valid[0], valid[-1])
        t0, t1 = seg[i0] / seg_len[i0], seg[i1] / seg_len[i1]
        turn = math.degrees(math.atan2(t0[0] * t1[1] - t0[1] * t1[0], np.dot(t0, t1)))
        tag = s["tag"]
        conv.append(dict(
            id=f"CV{tag}" if tag else f"CV_untagged_{i}",
            tag=tag,
            line=(str(tag)[:2] + "xx") if tag else None,
            kind="curve" if abs(turn) > 5.0 else "straight",   # 100mm 꺾임(작도 오차)은 직선으로 취급
            length_mm=round(L, 1),
            width_mm=float(widths.get((str(tag)[:2] + "xx") if tag else "default", widths["default"])),
            center_mm=[round(v, 1) for v in mid],
            heading_deg=round(math.degrees(math.atan2(tang[1], tang[0])), 2),
            start_mm=[round(float(v), 1) for v in poly[0]],
            end_mm=[round(float(v), 1) for v in poly[-1]],
            turn_deg=round(turn, 1),
            chord_mm=round(float(np.hypot(*chord)), 1),
            polyline_mm=[[round(float(x), 1), round(float(y), 1)] for x, y in poly],
        ))
    idx2id = {i: c["id"] for i, c in enumerate(conv)}
    link_json = [dict(src=idx2id[l["src"]], dst=idx2id[l["dst"]], type=l["type"]) for l in links]
    # 같은 쌍 중복 제거
    seen, uniq = set(), []
    for l in link_json:
        k = (l["src"], l["dst"])
        if k not in seen:
            seen.add(k); uniq.append(l)
    link_json = uniq

    # ---- 사람이 정한 해석 규칙 (config.yaml: interpretation) ----
    interp = cfg.get("interpretation", {})
    byid = {c["id"]: c for c in conv}
    has_in = {l["dst"] for l in link_json}
    # (a) 유입이 없는 짧은 평행 레인 → 같은 라인에서 옆으로 가장 가까운 구간으로부터 측면 이송
    if interp.get("orphan_lane_side_transfer"):
        maxd = float(interp.get("side_transfer_max_mm", 1000))
        for c in conv:
            if c["id"] in has_in or c["line"] in interp.get("source_lines", []):
                continue
            p0 = np.array(c["center_mm"])   # 시작점은 16x3/16x4 경계와 겹쳐 중앙점 사용
            cands = []
            for o in conv:
                if o["id"] == c["id"] or o["line"] != c["line"]:
                    continue
                d, _, _ = project(np.array(o["polyline_mm"]), p0)
                cands.append((d, o["id"]))
            d, src = min(cands)
            if d <= maxd:
                link_json.append(dict(src=src, dst=c["id"], type="side_transfer"))
                log.append(f"side transfer (config rule): {src} -> {c['id']} ({d:.0f}mm)")
            else:
                log.append(f"WARNING: {c['id']} has no inflow and no side-transfer source within {maxd}mm")
    # (b) 중심선이 접합 없이 교차하는 곳 → 입체 교차로 기록 (흐름 연결 없음)
    crossings = []
    for i, a in enumerate(conv):
        for b in conv[i + 1:]:
            if a["line"] == b["line"]:
                continue
            pa, pb = np.array(a["polyline_mm"]), np.array(b["polyline_mm"])
            for a0, a1 in zip(pa[:-1], pa[1:]):
                for b0, b1 in zip(pb[:-1], pb[1:]):
                    da, db = a1 - a0, b1 - b0
                    den = da[0] * db[1] - da[1] * db[0]
                    if abs(den) < 1e-9:
                        continue
                    t = ((b0 - a0)[0] * db[1] - (b0 - a0)[1] * db[0]) / den
                    u = ((b0 - a0)[0] * da[1] - (b0 - a0)[1] * da[0]) / den
                    if 0.01 < t < 0.99 and 0.01 < u < 0.99:
                        x = a0 + t * da
                        crossings.append(dict(a=a["id"], b=b["id"], at_mm=[round(float(x[0]), 1), round(float(x[1]), 1)],
                                              type=interp.get("crossing_type", "grade_separated")))
    # (c) 배출 끝(하류 연결이 없는 구간)
    has_out = {l["src"] for l in link_json}
    sinks = [c["id"] for c in conv if c["id"] not in has_out]
    sources = [c["id"] for c in conv if c["id"] not in {l["dst"] for l in link_json}]

    # IoT → 가장 가까운 구간
    iot_json = []
    for k, d in enumerate(sorted(iots, key=lambda d: (d["pos"][1], d["pos"][0]))):
        dist, i = min((project(s["poly"], d["pos"])[0], i) for i, s in enumerate(sections))
        iot_json.append(dict(id=f"IOT{k+1:03d}", pos_mm=[round(float(v), 1) for v in d["pos"]],
                             radius_mm=d["radius"], conveyor=idx2id[i], dist_to_conveyor_mm=round(dist, 1),
                             dxf_handle=d["handle"]))
    # 제어반: 블록 + 가장 가까운 MTEXT 이름
    cp_json = []
    for c in cps:
        r = c["rect"]
        mn, mx = r.min(0), r.max(0)
        ctr = (mn + mx) / 2
        name = min(texts, key=lambda t: np.hypot(*(t["pos"] - ctr)))
        dname = float(np.hypot(*(name["pos"] - ctr)))
        size = mx - mn
        dims = c["block"].lower().replace("cp", "").strip().split("x")
        nominal = [float(dims[0]), float(dims[1])]
        # 문 방향: 호 중심이 놓인 변 → 바깥쪽 법선
        door_dir = None
        if c["door"]:
            dc = np.mean([d["center"] for d in c["door"]], 0)
            v = dc - ctr
            v = v / (np.linalg.norm(v) + 1e-9)
            # 사각형의 짧은 변 방향 축 기준으로 문 방향
            door_dir = [round(float(v[0]), 3), round(float(v[1]), 3)]
        nearest_cv, dcv = None, None
        dist, i = min((project(s["poly"], ctr)[0], i) for i, s in enumerate(sections))
        cp_json.append(dict(id=name["text"], block=c["block"], layer=c["layer"],
                            center_mm=[round(float(v), 1) for v in ctr],
                            footprint_mm=[round(float(size[0]), 1), round(float(size[1]), 1)],
                            nominal_mm=nominal, door_dir=door_dir,
                            outline_mm=[[round(float(x), 1), round(float(y), 1)] for x, y in r],
                            door_arcs_mm=[[[round(x, 1), round(y, 1)] for x, y in d["poly"]] for d in c["door"]], label_dist_mm=round(dname, 1),
                            nearest_conveyor=idx2id[i], dist_to_conveyor_mm=round(dist, 1),
                            dxf_handle=c["handle"]))
    cp_json.sort(key=lambda c: int(c["id"][2:]) if c["id"][2:].isdigit() else 999)

    all_pts = np.vstack([s["poly"] for s in sections])
    origin = all_pts.min(0)
    out = dict(
        source=str(DXF.relative_to(ROOT)),
        units="mm",
        origin_mm=[round(float(v), 1) for v in origin],
        notes=["flow direction inferred from TAGNUMBER ascending order along each line",
               "conveyor width not in drawing (centerline only); width_mm from config.yaml",
               "16xx lanes cross the 15xx top loop without connection -> recorded as grade-separated crossings (user decision)",
               "16x6-16x7 parallel lanes fed by side transfer from nearest 16xx section (user decision)"],
        log=log,
        conveyors=conv,
        links=link_json,
        crossings=crossings,
        sources=sources,
        sinks=sinks,
        control_panels=cp_json,
        iot=iot_json,
    )
    with open(OUT / "equipment.json", "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)

    # --------------- 요약
    print("\n".join(log))
    tagged = [c for c in conv if c["tag"]]
    print(f"\nsections={len(conv)} tagged={len(tagged)} untagged={len(conv)-len(tagged)}")
    lines = defaultdict(list)
    for c in tagged:
        lines[c["line"]].append(c)
    for k in sorted(lines):
        L = [c["length_mm"] for c in lines[k]]
        tg = sorted(c["tag"] for c in lines[k])
        print(f"  line {k}: n={len(L)} tags {tg[0]}-{tg[-1]} len min/med/max={min(L):.0f}/{np.median(L):.0f}/{max(L):.0f}"
              f" curves={sum(c['kind']=='curve' for c in lines[k])}")
    lt = defaultdict(int)
    for l in link_json:
        lt[l["type"]] += 1
    print("links:", dict(lt))
    for l in link_json:
        if l["type"] != "inline":
            print("  ", l)
    dd = [d["dist_to_conveyor_mm"] for d in iot_json]
    per = defaultdict(int)
    for d in iot_json:
        per[d["conveyor"]] += 1
    print(f"iot={len(iot_json)} dist to conveyor min/med/max={min(dd):.0f}/{np.median(dd):.0f}/{max(dd):.0f};"
          f" conveyors with 0/1/2+ iot: {sum(1 for c in conv if per[c['id']]==0)}/"
          f"{sum(1 for c in conv if per[c['id']]==1)}/{sum(1 for c in conv if per[c['id']]>=2)}")
    print(f"crossings={len(crossings)}: {[(c['a'], c['b']) for c in crossings]}")
    print(f"sources={sources}")
    print(f"sinks={sinks}")
    print(f"control panels={len(cp_json)}")
    for c in cp_json:
        print(f"   {c['id']:5s} {c['block']:12s} fp={c['footprint_mm']} door={c['door_dir']} "
              f"near={c['nearest_conveyor']} ({c['dist_to_conveyor_mm']:.0f}mm) label_d={c['label_dist_mm']:.0f}")
    print("wrote", OUT / "equipment.json")


if __name__ == "__main__":
    main()
