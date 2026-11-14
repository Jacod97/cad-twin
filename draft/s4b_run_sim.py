"""Step 4b: sim_model.json + sim_config.yaml → SimPy 모델 생성·실행 → out/sim_results.json, out/sim_results.csv

모델 (구간 = 축적 컨베이어 존):
  - 용량 = ceil(길이 / pitch) 개, 통과 시간 = 길이 / 속도
  - 다음 구간에 자리가 날 때까지 현재 구간 자리를 붙잡고 기다림(blocking) → 정체가 뒤로 전파
  - 구간 입구는 pitch/속도 간격으로만 들어갈 수 있음(헤드웨이) → 라인 최대 처리량 = 속도/pitch
  - 합류: 들어가려는 물품끼리 선착순. 분기/측면이송: 추가 동작 시간
  - 경로: 목적지까지 최단 경로의 다음 구간. 목적 레인이 꽉 차면(설정 시) 루프를 계속 돈다
  - 출구(sink): 레인 끝에서 1개씩 service_s 동안 처리

통계 (warmup 이후):
  throughput_per_h, avg_wait_s(다음 구간 진입 대기), utilization(평균 점유/용량), max_occupancy
병목 판정: 대기가 큰 구간 바로 하류이면서 자신은 대기가 작은 구간 = 막힘의 원인 (+ 출구 처리 포화)
"""
import csv
import json
import math
import random
from pathlib import Path

import networkx as nx
import simpy
import yaml

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


class Section:
    def __init__(self, env, node, cfg):
        self.env = env
        self.id = node["id"]
        self.line = node["line"]
        self.role = node["role"]
        self.length = node["length_m"]
        sp = cfg["conveyor"]["speed_mps"]
        self.speed = float(sp.get(self.line, sp["default"]))
        it = cfg["item"]
        self.pitch = float(it["pitch_m"]) if "pitch_m" in it else float(it["length_m"]) + float(it["gap_m"])
        # 연속 컨베이어: 구간 위 최대 물품 수 = ceil(L/pitch) → 최대 처리량 = 속도/pitch (구간 길이와 무관)
        self.cap = max(1, int(math.ceil(self.length / self.pitch - 1e-9)))
        self.transit = self.length / self.speed
        self.headway = self.pitch / self.speed
        self.slots = simpy.Resource(env, self.cap)
        self.gate = simpy.Resource(env, 1)
        self.occ = 0            # 구간 위에 실제로 있는 물품 수
        self.reset_stats()

    def reset_stats(self):
        self.n_in = self.n_out = 0
        self.wait_sum = 0.0
        self.wait_n = 0
        self.occ_area = 0.0
        self.max_occ = self.occ
        self._t = self.env.now

    def _acc(self):
        now = self.env.now
        self.occ_area += self.occ * (now - self._t)
        self._t = now

    def entered(self):
        self._acc()
        self.occ += 1
        self.n_in += 1
        self.max_occ = max(self.max_occ, self.occ)

    def left(self):
        self._acc()
        self.occ -= 1
        self.n_out += 1

    def full(self):
        return self.slots.count >= self.cap


class Model:
    def __init__(self, model, cfg):
        self.cfg = cfg
        self.env = simpy.Environment()
        self.rng = random.Random(cfg["run"]["seed"])
        self.G = nx.DiGraph()
        for n in model["nodes"]:
            self.G.add_node(n["id"], **n)
        for e in model["edges"]:
            self.G.add_edge(e["src"], e["dst"], type=e["type"])
        self.sec = {n["id"]: Section(self.env, n, cfg) for n in model["nodes"]}
        self.sources = model["sources"]
        self.sinks = model["sinks"]
        sk = cfg["sinks"]
        d = sk["default"]
        self.sink_service = {s: float((sk.get(s) or {}).get("service_s", d["service_s"])) for s in self.sinks}
        self.service_dist = sk.get("service_dist", "constant")
        self.sink_weight = {s: float((sk.get(s) or {}).get("weight", d["weight"])) for s in self.sinks}
        self.picker = {s: simpy.Resource(self.env, 1) for s in self.sinks}
        self.picker_busy = {s: 0.0 for s in self.sinks}
        # 목적지별 각 노드의 후보 다음 구간 (목적지까지 거리 짧은 순)
        for a, b in self.G.edges:
            self.G.edges[a, b]["w"] = self.sec[b].length
        self.dist = {s: nx.single_source_dijkstra_path_length(self.G.reverse(copy=False), s, weight="w")
                     for s in self.sinks}
        self.done = []          # (t_created, t_done, dest, laps)
        self.source_wait = {s: [] for s in self.sources}
        self.recirc = 0
        self.max_wait = (0.0, None)   # 워밍업 이후 한 물품이 한 구간에서 기다린 최장 시간
        self.last_move = 0.0
        self.warm = False

    # ------------------------------------------------------------ 경로
    def candidates(self, cur, dest):
        dd = self.dist[dest]
        c = [n for n in self.G.successors(cur) if n in dd]
        return sorted(c, key=lambda n: dd[n] + self.sec[n].length)

    def extra_time(self, a, b):
        t = self.G.edges[a, b]["type"]
        if t == "divert":
            return float(self.cfg["conveyor"]["divert_time_s"])
        if t == "side_transfer":
            return float(self.cfg["conveyor"]["side_transfer_time_s"])
        return 0.0

    # ------------------------------------------------------------ 프로세스
    def item(self, src, dest):
        env = self.env
        t0 = env.now
        sec = self.sec[src]
        req = sec.slots.request()
        yield req
        g = sec.gate.request()
        yield g
        env.process(self._hold(sec.gate, g, sec.headway))
        sec.entered()
        if self.warm:
            self.source_wait[src].append(env.now - t0)
        laps = 0
        while True:
            yield env.timeout(sec.transit)
            if sec.id == dest:
                with self.picker[dest].request() as p:
                    yield p
                    ts = env.now
                    mean = self.sink_service[dest]
                    yield env.timeout(self.rng.expovariate(1 / mean) if self.service_dist == "exponential" else mean)
                    if self.warm:
                        self.picker_busy[dest] += env.now - ts
                sec.left()
                sec.slots.release(req)
                if self.warm:
                    self.done.append((t0, env.now, dest, laps))
                self.last_move = env.now
                return
            cands = self.candidates(sec.id, dest)
            nxt = self.sec[cands[0]]
            if (self.cfg["conveyor"]["recirculate_if_blocked"] and len(cands) > 1 and nxt.full()
                    and self.G.edges[sec.id, nxt.id]["type"] in ("divert", "side_transfer")):
                nxt = self.sec[cands[1]]
                laps += 1
                if self.warm:
                    self.recirc += 1
            tw = env.now
            nreq = nxt.slots.request()
            yield nreq
            g = nxt.gate.request()
            yield g
            sec.wait_sum += env.now - tw
            sec.wait_n += 1
            if self.warm and env.now - tw > self.max_wait[0]:
                self.max_wait = (env.now - tw, sec.id)
            extra = self.extra_time(sec.id, nxt.id)
            env.process(self._hold(nxt.gate, g, nxt.headway + extra))
            if extra:                       # 분기·이송 동작 동안 현재 구간(이송기)을 점유
                yield env.timeout(extra)
            sec.left()
            sec.slots.release(req)
            nxt.entered()
            self.last_move = env.now
            sec, req = nxt, nreq

    def _hold(self, res, req, t):
        """입구 간격(헤드웨이): 물품 이동과 별개로 게이트를 t초 점유."""
        yield self.env.timeout(t)
        res.release(req)

    def source(self, src):
        rate = float(self.cfg["sources"][src]["rate_per_h"]) / 3600
        dests = list(self.sink_weight)
        weights = [self.sink_weight[d] for d in dests]
        while True:
            yield self.env.timeout(self.rng.expovariate(rate))
            dest = self.rng.choices(dests, weights)[0]
            self.env.process(self.item(src, dest))

    def warmup(self, t):
        yield self.env.timeout(t)
        for s in self.sec.values():
            s._acc()
            s.reset_stats()
        self.warm = True

    def watchdog(self, period=300):
        while True:
            yield self.env.timeout(period)
            if self.env.now - self.last_move > period:
                print(f"WARNING: no movement for {period}s at t={self.env.now:.0f} (gridlock?)")
                return

    def run(self):
        for s in self.sources:
            self.env.process(self.source(s))
        self.env.process(self.warmup(self.cfg["run"]["warmup_s"]))
        self.env.process(self.watchdog())
        T = self.cfg["run"]["duration_s"]
        self.env.run(until=T)
        for s in self.sec.values():
            s._acc()
        return T - self.cfg["run"]["warmup_s"]


def main():
    model = json.load(open(OUT / "sim_model.json"))
    cfg = yaml.safe_load(open(HERE / "sim_config.yaml"))
    m = Model(model, cfg)
    span = m.run()

    rows = []
    for s in m.sec.values():
        rows.append(dict(id=s.id, line=s.line, role=s.role, length_m=round(s.length, 3), capacity=s.cap,
                         speed_mps=s.speed,
                         throughput_per_h=round(s.n_out / span * 3600, 1),
                         avg_wait_s=round(s.wait_sum / s.wait_n, 2) if s.wait_n else 0.0,
                         utilization=round(s.occ_area / span / s.cap, 3),
                         max_occupancy=s.max_occ))
    by = {r["id"]: r for r in rows}
    # 병목(원인): 출구 처리 가동률이 높은 sink, 또는 자신은 안 막혔는데 바로 상류가 크게 막힌 구간
    # 정체(결과): 다음 구간 진입 대기가 큰 구간
    WAIT_HI, WAIT_LO, PICK_HI = 5.0, 1.0, 0.85
    for r in rows:
        ups = [by[u] for u in m.G.predecessors(r["id"])]
        r["congested"] = bool(r["avg_wait_s"] >= WAIT_HI)
        r["bottleneck"] = bool(ups and r["avg_wait_s"] < WAIT_LO and r["utilization"] > 0.5
                               and max(u["avg_wait_s"] for u in ups) > WAIT_HI)
        if r["role"] == "sink":
            r["picker_utilization"] = round(m.picker_busy[r["id"]] / span, 3)
            if r["picker_utilization"] >= PICK_HI:
                r["bottleneck"] = True

    lead = [b - a for a, b, _, _ in m.done]
    summary = dict(
        sim_span_s=span,
        completed=len(m.done),
        completed_per_h=round(len(m.done) / span * 3600, 1),
        offered_per_h=sum(float(cfg["sources"][s]["rate_per_h"]) for s in m.sources),
        avg_lead_time_s=round(sum(lead) / len(lead), 1) if lead else None,
        recirculations=m.recirc,
        avg_source_wait_s={s: round(sum(v) / len(v), 2) if v else None for s, v in m.source_wait.items()},
        bottlenecks=[r["id"] for r in rows if r["bottleneck"]],
        congested=[r["id"] for r in rows if r["congested"]],
        max_avg_wait=max(({"id": r["id"], "avg_wait_s": r["avg_wait_s"]} for r in rows), key=lambda x: x["avg_wait_s"]),
        max_single_wait=dict(id=m.max_wait[1], wait_s=round(m.max_wait[0], 1)),
        max_lead_time_s=round(max(lead), 1) if lead else None,
    )
    json.dump(dict(summary=summary, sections=rows, config=cfg), open(OUT / "sim_results.json", "w"), indent=1)
    keys = list(rows[0].keys()) + ["picker_utilization"]
    with open(OUT / "sim_results.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(dict.fromkeys(keys)))
        wr.writeheader()
        wr.writerows(rows)

    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print("\ntop 10 by avg_wait_s:")
    for r in sorted(rows, key=lambda r: -r["avg_wait_s"])[:10]:
        print(f"  {r['id']:7s} {r['role']:9s} thr={r['throughput_per_h']:7.1f}/h wait={r['avg_wait_s']:6.2f}s "
              f"util={r['utilization']:.2f} cap={r['capacity']}")
    print("sinks:")
    for r in rows:
        if r["role"] == "sink":
            print(f"  {r['id']} thr={r['throughput_per_h']:6.1f}/h picker_util={r['picker_utilization']:.2f} "
                  f"lane_util={r['utilization']:.2f}")
    print("wrote", OUT / "sim_results.json", OUT / "sim_results.csv")


if __name__ == "__main__":
    main()
