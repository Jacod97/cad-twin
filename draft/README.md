# CAD 배치도 → USD 디지털 트윈 → SimPy 시뮬레이션 (데모 프로토타입)

환경: conda `torch_render` (추가 설치: matplotlib, simpy, pyrender, PyOpenGL, trimesh). 렌더는 EGL로 GPU 오프스크린.

```bash
PY=/disk2/jaesik/miniconda3/envs/torch_render/bin/python
cd draft
$PY s1_analyze_dxf.py        # DXF → out/equipment.json
$PY s1_plot_plan.py          # → out/s1_plan_*.png (도면 위 추출 결과)
$PY s2_build_scene.py        # → out/scene/factory.usda
$PY s2_render.py             # → out/s2_top_overlay*.png (렌더+도면선), out/s2_view_{overview,sortlanes,crossing}.png
$PY s4a_build_sim_model.py   # → out/sim_model.json, sim_config.yaml(없을 때만)
$PY s4b_run_sim.py           # → out/sim_results.json / .csv
$PY s4c_color_scene.py       # → out/scene/factory_sim.usda, out/s4_*.png
```

| 파일 | 사람이 고치는 값 |
|---|---|
| `config.yaml` | 컨베이어 폭, 도면 해석 규칙(평행 레인 측면 이송, 교차=입체), 에셋 경로, 제어반 높이, IoT 표시 |
| `sim_config.yaml` | 속도, 물품 pitch, 분기/이송 시간, 인입 도착률, 목적지 비율·처리 시간 (`# TODO` = 가정값) |

## 도면 해석 요약
- 레이어 0 LINE/ARC = 컨베이어 중심선, 200mm 직교 짧은 선 = 구간 경계 틱
  (`*U28/*U30/*U32` = 인입 라인 3개의 틱 23개 묶음)
- 블록 `name`(TAGNUMBER) 191개 ↔ 구간 191개 1:1. 흐름 방향 = 번호 증가 방향
- 11xx·12xx·13xx 인입 → 14xx 메인(30° 사선 합류 2곳) → 15xx 재순환 루프 → 16xx 분류 레인 9개(하단 루프에서 분기)
- 16xx 레인은 상단 루프를 접합 없이 가로지름 → 입체 교차(흐름 연결 없음). 3D에서는 교차 구간 16x3을 롤러 상면 2.0m로 높이고
  앞(16x1–16x2)·뒤(16x4–16x5, 평행 16x6–16x7) 구간을 경사로로 연결, 아래에 지지 기둥 (`config.yaml: grade_separation`)
- 16x6–16x7 평행 레인은 16x4에서 측면 이송 (사용자 결정)
- 16xx 레인 중간 100mm 어긋남 = 작도 오차로 보고 이어 붙임

## USD 장면 구조
- `/World/Conveyors/L11xx/CV1101` … Xform + `Geom`(에셋 reference, instanceable). 속성 `cadtwin:tag`, `line`, `kind`,
  `length_mm`, `width_mm`, `upstream`/`downstream`, `crosses`, `asset_ref`, `dxf_start_mm` …
- `/World/ControlPanels/CPn` 크기 맞춘 박스(+문 면), `/World/IoT/IOTnnn` 표시 구, `/World/Floor`
- `factory_sim.usda` = factory.usda sublayer + 컨베이어 prim에 `sim:*` 속성 + `/World/SimOverlay` 색 띠·병목 기둥
- 좌표: 월드(m) = (DXF(mm) − `origin_mm`) / 1000, Z-up

## 알려진 한계
- 곡선 에셋은 XY 균일 스케일이라 폭을 따로 못 맞춤(r=1040mm에서 폭 0.80m로 우연히 일치). 60° 곡선은 단순 곡선판
- 715–930mm 짧은 구간은 2m 직선 에셋을 길이 방향으로 크게 줄여서 롤러가 촘촘해 보임
- 경사로 최대 기울기 약 23° (2구간으로 1.23m를 오르내림). 기울인 에셋은 다리도 함께 기울어 보임
- 제어반 24개는 도면상 컨베이어 폭(800mm 가정) 안에 겹쳐 있어 옆으로 밀어 배치(`cadtwin:moved_mm`에 기록)
- 시뮬레이션: 구간 = 용량 ceil(L/pitch)의 축적 존, 블로킹 전파, 합류 선착순. 가감속·물품 크기 차이·제어 로직은 없음
