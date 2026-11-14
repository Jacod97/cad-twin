"""Step 4a: 도면에서 뽑은 연결 관계(equipment.json) → 시뮬레이션 모델 뼈대(sim_model.json)
          + 사람이 채우는 설정 파일(sim_config.yaml, 없을 때만 생성).

sim_model.json
  nodes : 컨베이어 구간 (id, line, length_m, kind, role[source|sink|divert|merge|transfer|conveyor])
  edges : src→dst, type(inline|merge|divert|side_transfer)
  sources / sinks / crossings(흐름 연결 없음, 참고용)
"""
import json
from pathlib import Path

import networkx as nx

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
EQ = json.load(open(OUT / "equipment.json"))

CONFIG_TEMPLATE = """\
# SimPy 시뮬레이션 설정. s4a가 처음 한 번만 만든다(있으면 덮어쓰지 않음).
# "# TODO" 표시는 현장 값으로 바꿔야 하는 가정값.

run:
  duration_s: 7200          # 시뮬레이션 길이 (2시간)
  warmup_s: 900             # 통계에서 제외할 초기 구간
  seed: 1

item:
  length_m: 0.6             # TODO 물품 길이
  gap_m: 0.5                # TODO 물품 간 최소 간격. 구간 용량 = ceil(길이 / (length+gap))

conveyor:
  speed_mps:                # TODO 라인별 컨베이어 속도
    default: 1.0
    "16xx": 0.8
  divert_time_s: 1.0        # TODO 분기(직각 이송) 동작 시간
  side_transfer_time_s: 1.5 # TODO 16x4 → 16x6 측면 이송 시간
  recirculate_if_blocked: true  # 목적 레인이 꽉 차면 루프를 한 바퀴 더 돈다

sources:                    # TODO 인입 라인별 도착률 (개/시간), 도착 간격은 지수분포
{sources}

sinks:                      # 분류 목적지(레인 끝). service_s = 1개 처리(작업자 픽업 등) 평균 시간
  service_dist: exponential # exponential | constant
  default:
    weight: 1.0             # TODO 목적지 비율 (상대값)
    service_s: 20.0         # TODO
{sinks}
"""


def main():
    links = EQ["links"]
    G = nx.DiGraph()
    for c in EQ["conveyors"]:
        G.add_node(c["id"], line=c["line"], length_m=c["length_mm"] / 1000, kind=c["kind"])
    for l in links:
        G.add_edge(l["src"], l["dst"], type=l["type"])

    roles = {}
    for n in G.nodes:
        outs = [G.edges[n, m]["type"] for m in G.successors(n)]
        ins = [G.edges[m, n]["type"] for m in G.predecessors(n)]
        if not ins:
            r = "source"
        elif not outs:
            r = "sink"
        elif "divert" in outs:
            r = "divert"
        elif "side_transfer" in outs:
            r = "transfer"
        elif "merge" in ins:
            r = "merge"
        else:
            r = "conveyor"
        roles[n] = r
        G.nodes[n]["role"] = r

    sources = [n for n in G if roles[n] == "source"]
    sinks = [n for n in G if roles[n] == "sink"]
    # 검증: 모든 출구에 어떤 입구에서든 도달 가능한가, 순환(루프) 확인
    unreachable = [s for s in sinks if not any(nx.has_path(G, a, s) for a in sources)]
    cycles = list(nx.simple_cycles(G))
    model = dict(
        nodes=[dict(id=n, **G.nodes[n]) for n in G.nodes],
        edges=[dict(src=a, dst=b, type=G.edges[a, b]["type"]) for a, b in G.edges],
        sources=sources, sinks=sinks, crossings=EQ["crossings"],
        checks=dict(unreachable_sinks=unreachable, n_cycles=len(cycles),
                    cycle_lengths=[len(c) for c in cycles]),
    )
    json.dump(model, open(OUT / "sim_model.json", "w"), indent=1)

    cnt = {}
    for r in roles.values():
        cnt[r] = cnt.get(r, 0) + 1
    print(f"nodes={G.number_of_nodes()} edges={G.number_of_edges()} roles={cnt}")
    print(f"sources={sources}")
    print(f"sinks({len(sinks)})={sinks}")
    print(f"divert points: {[n for n in G if roles[n]=='divert']}")
    print(f"merge points : {[n for n in G if roles[n]=='merge']}")
    print(f"cycles: {len(cycles)} (lengths {[len(c) for c in cycles]}) unreachable sinks: {unreachable}")
    print("wrote", OUT / "sim_model.json")

    cfg_path = HERE / "sim_config.yaml"
    if cfg_path.exists():
        print("sim_config.yaml exists - not overwritten")
    else:
        src_txt = "\n".join(f"  {s}: {{rate_per_h: 900}}   # TODO" for s in sources)
        sink_txt = "\n".join(f"  {s}: {{}}" for s in sinks)
        cfg_path.write_text(CONFIG_TEMPLATE.format(sources=src_txt, sinks=sink_txt))
        print("wrote", cfg_path)


if __name__ == "__main__":
    main()
