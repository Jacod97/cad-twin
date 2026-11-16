# draft/ 코드 설명

작성일 2026-11-16 | 기준: `draft/` 소스 코드 (커밋 5d3c79d). 코드를 실행하지 않고 읽어서 정리함.

## 1. 데이터 변환 흐름

`draft/`의 script들은 CAD 도면 한 장을 차례로 다른 형태의 데이터로 바꾸는 과정을 구현한다. 각 script는 앞 script가 만든 파일을 읽어 새 형태의 데이터를 만든다. 전체 흐름은 아래와 같다.

```text
DXF 도면 (선·호·block entity, mm)
  │ s1_analyze_dxf.py
  ▼
equipment.json (구간 목록 + 구간 사이 흐름 link + 제어반 + IoT, mm)
  │ s2_build_scene.py                       │ s4a_build_sim_model.py
  ▼                                         ▼
factory.usda (3D 장면 prim 트리, m)       sim_model.json (방향 graph + node role)
  │                                         │ s4b_run_sim.py  (+ sim_config.yaml)
  │                                         ▼
  │                                       sim_results.json / .csv (구간별 통계)
  │ s4c_color_scene.py ◀────────────────────┘
  ▼
factory_sim.usda (factory.usda + 구간별 통계 속성 + 색 띠)
  │ s5_export_usdz.py
  ▼
factory_sim_{full,lite}.usdz (Quick Look용 단일 package)
```

> ※ 용어 설명
> - DXF (Drawing Exchange Format): AutoCAD 도면을 선·호·block 같은 entity 목록으로 저장하는 텍스트 형식
> - USD (Universal Scene Description): Pixar가 만든 3D 장면 기술 형식. 장면은 prim이라는 node의 계층 트리이고, `.usda`는 텍스트, `.usdc`는 바이너리 저장 형식
> - prim: USD 장면 트리의 node 하나. 경로(`/World/Conveyors/...`)로 가리키고, 형상·변환·사용자 정의 속성을 가짐
> - USDZ: USD 파일과 texture를 압축 없는 zip 하나로 묶은 package 형식. macOS·iOS의 Quick Look이 열 수 있음
> - 구간: 도면의 conveyor 중심선을 경계 표시에서 자른 한 토막. 이 code에서 데이터의 기본 단위이며, JSON 항목 하나, USD prim 하나, simulation node 하나에 대응함. id는 `CV` + 도면 이름표 번호 (예: `CV1101`)

이 흐름에 직접 끼지 않는 파일은 세 종류다.

- 설정 파일 `config.yaml`, `sim_config.yaml`: 도면에 없는 값(폭, 높이, 속도 등)을 사람이 넣는 곳. 변환 과정의 추가 입력
- 확인용 script `s1_plot_plan.py`, `s2_render.py`: 변환 결과를 원본 도면과 겹쳐 그려 변환이 맞았는지 보는 이미지 생성
- 공용 module `usd_render.py`: USD 장면을 이미지로 그리는 함수 모음. 확인용 script와 `s4c_color_scene.py`가 씀

`README.md`는 실행 명령과 도면 해석 규칙, 장면 구조, 알려진 한계를 적은 문서다. 파일 이름에 3단계(`s3_*`)는 없다.

### 좌표 변환

DXF 데이터는 mm 단위 평면 좌표다. USD 장면부터는 m 단위 3D 좌표이고 Z축이 위(Z-up)다. 평면 좌표는 모든 script에서 식 (1)로 바꾼다 (`s2_build_scene.py:42`, `s2_render.py:164`, `s4c_color_scene.py:53`).

$$
\mathbf{p}_{\mathrm{world}} = \frac{\mathbf{p}_{\mathrm{DXF}} - \mathbf{o}}{1000} \tag{1}
$$

- $\mathbf{p}_{\mathrm{world}}$: USD world 좌표 (m)
- $\mathbf{p}_{\mathrm{DXF}}$: DXF 도면 좌표 (mm)
- $\mathbf{o}$: `equipment.json`의 `origin_mm`. 모든 구간 점의 x·y 최솟값 (mm)

### script 사이의 import

몇몇 script는 앞 script의 함수를 import해 같은 변환을 다시 쓴다 (표 1). `s1_plot_plan.py`, `s2_build_scene.py`, `s4c_color_scene.py`는 module 최상단에서 `out/equipment.json` 등을 읽으므로 import만 해도 그 파일이 있어야 한다.

**표 1. script 사이의 import**

| 가져다 쓰는 파일 | 가져오는 함수 | 다시 쓰는 변환 |
|---|---|---|
| `s2_render.py` | `s1_plot_plan.dxf_paths` | DXF → polyline 목록 |
| `s4c_color_scene.py` | `s2_build_scene.junction_trims`, `trimmed_poly`, `elevations` | 구간 끝 자르기, 구간 높이 계산 |
| `s5_export_usdz.py` (lite mode) | `s4c_color_scene.section_polys`, `section_elev`, `ribbon` | 구간 중심선 → 띠 형상 |

## 2. 설정 파일

두 YAML 파일은 도면에 없는 값을 변환 과정에 넣는다. 아래 표는 각 key가 어느 변환 단계에 들어가는지를 보인다.

### 2.1 config.yaml

**표 2. config.yaml의 key와 쓰이는 변환**

| key | 의미 | 쓰이는 변환 |
|---|---|---|
| `conveyor.width_mm` | line별 conveyor 폭 (mm). 도면에는 중심선만 있음 | 구간 JSON의 `width_mm` (`s1_analyze_dxf.py:497`) |
| `interpretation.source_lines` | 외부 유입 line 목록 | 측면 이송 link 추가 대상에서 제외 (`s1_analyze_dxf.py:524`) |
| `interpretation.orphan_lane_side_transfer`, `side_transfer_max_mm` | 유입이 없는 평행 lane을 옆 구간에서 측면 이송받도록 연결할지, 그 최대 거리 | `side_transfer` link 추가 (`s1_analyze_dxf.py:521`) |
| `interpretation.crossing_type` | 접합 없는 교차의 종류 기록값 | `crossings` 항목의 `type` (`s1_analyze_dxf.py:557`) |
| `assets.straight`, `assets.curve90` | 직선·90° 곡선 conveyor 3D asset 경로 | 구간 prim의 reference 대상 (`s2_build_scene.py:347-348`) |
| `control_panel.height_mm`, `clear_overlap`, `clearance_mm` | 제어반 높이, 겹침 해소 여부, 간격 | 제어반 prim의 크기·위치 (`s2_build_scene.py:456-460`) |
| `iot.marker_height_mm`, `marker_radius_mm` | IoT 표시 구의 높이·반지름 | IoT prim (`s2_build_scene.py:495-496`) |
| `grade_separation.*` | 입체 교차로 높일 line, 높이, 경사로 구간 수, 기둥 간격 | 구간 높이와 지지 기둥 (`s2_build_scene.py:235-283`, `:409`) |

> ※ 용어 설명
> - 측면 이송(side transfer): 나란히 놓인 conveyor 사이에서 물품을 옆으로 옮기는 동작
> - 입체 교차(grade separation): 평면상 교차하는 두 conveyor의 높이를 달리해 서로 연결되지 않게 한 구조. 이 code에서는 16xx lane이 15xx loop 위를 지나감

### 2.2 sim_config.yaml

`s4b_run_sim.py`만 읽는다. `s4a_build_sim_model.py`는 이 파일이 없을 때만 template을 만든다.

**표 3. sim_config.yaml의 key와 쓰이는 변환**

| key | 의미 | 쓰이는 변환 |
|---|---|---|
| `run.duration_s`, `warmup_s`, `seed` | 실행 길이, 통계 제외 초기 시간 (s), 난수 seed | simulation 실행 범위 |
| `item.length_m`, `gap_m` (또는 `pitch_m`) | 물품 길이와 간격 (m) | 구간 용량과 입구 간격 (식 (4), (6)) |
| `conveyor.speed_mps` | line별 속도 (m/s), line key가 없으면 `default` | 구간 통과 시간 (식 (5)) |
| `conveyor.divert_time_s`, `side_transfer_time_s` | 분기·측면 이송 추가 시간 (s) | 해당 link를 지날 때 지연 |
| `conveyor.recirculate_if_blocked` | 목적 lane이 꽉 차면 loop를 더 돌지 | 경로 선택 |
| `sources.<구간 id>.rate_per_h` | 인입 구간별 도착률 (개/h) | 물품 생성 간격 |
| `sinks.default`, `sinks.<구간 id>` (`weight`, `service_s`), `service_dist` | 목적지 선택 비율, 출구 처리 시간 (s)과 분포 | 물품 목적지와 출구 처리 |

> ※ 용어 설명
> - pitch: 물품 하나가 conveyor 위에서 차지하는 길이. 물품 길이 + 최소 간격
> - sink: 물품이 시스템을 빠져나가는 출구 구간

## 3. DXF → equipment.json

### 3.1 s1_analyze_dxf.py

#### 역할

도면의 선 entity를 conveyor 구간 목록과 구간 사이 흐름 연결로 바꾼다. 도면에는 중심선, 경계 표시, 이름표만 있고 "구간"이나 "연결"이라는 데이터는 없다. 이 script는 선의 기하 관계와 이름표 번호 순서로 그것을 만들어 낸다.

> ※ 용어 설명
> - ezdxf: DXF 파일을 읽는 Python library
> - layer: DXF entity를 묶는 분류 이름. 중심선은 layer `0`, 제어반 이름은 layer `CP`
> - block / INSERT: block은 재사용하는 도형 묶음 정의, INSERT는 그 block을 도면에 놓은 entity
> - TAGNUMBER: block `name`의 속성값. 구간 이름표 번호(예: 1101)
> - tick: 중심선에 직교하는 길이 200 mm 짧은 선. 구간 경계를 표시함
> - polyline: 점을 순서대로 이은 꺾은선. 이 code에서는 `(N, 2)` numpy 배열 (mm)
> - OCS / WCS: DXF의 객체 좌표계와 world 좌표계. 좌우 반전된 block은 OCS를 WCS로 바꿔야 실제 위치가 나옴

#### 입력 데이터

- `data/dxf/designscad_conveyor-system-sorting-lane-plan-with-control-poi_1301.dxf` (project root 기준, `:24`)
- `config.yaml`의 `conveyor.width_mm`, `interpretation.*`

#### 변환 과정

데이터는 아래 순서로 형태가 바뀐다.

**(1) DXF entity → 종류별 원시 목록** (`read_dxf()`, `:96`)

| DXF entity | 판별 조건 | 바뀐 데이터 |
|---|---|---|
| layer `0`의 LINE | 길이 200 ± 1 mm | tick: (중점, 방향 단위 vector) |
| layer `0`의 LINE | 그 밖 | 중심선 조각: 2점 polyline |
| layer `0`의 ARC | – | 중심선 조각: 1° 간격 점으로 근사한 polyline |
| 이름이 `*U`로 시작하는 INSERT | 안쪽 중첩 INSERT까지 풀어 LINE 추출 | tick (infeed line 3개의 tick 묶음) |
| 이름이 `name`인 INSERT | 속성 `TAGNUMBER` | 이름표: (번호, 위치) |
| 이름이 `IOT`인 INSERT | 안의 CIRCLE | IoT: (중심 WCS, 반지름, handle) |
| 이름이 `cp`로 시작하는 INSERT | 안의 LWPOLYLINE, ARC | 제어반: (외곽 사각형 WCS, 문 열림 호) |
| layer `CP`의 MTEXT | – | 제어반 이름: (텍스트, 위치) |

**(2) 중심선 조각 → graph → chain** (`build_chains()`, `:160`)

조각들은 서로 연결 정보가 없는 선분 묶음이다. 이것을 node(끝점)와 edge(조각)의 graph로 바꾼 뒤, 곧게 이어지는 edge끼리 이어 긴 chain으로 만든다.

1. 끝점을 2 mm(`SNAP`) 안에서 같은 node로 묶음 → edge 목록 `(a, b, polyline)`
2. 한 조각의 끝점이 다른 조각 내부에 닿으면(T 접합) 그 조각을 그 점에서 둘로 쪼갬
3. 연결이 하나뿐인 끝점끼리 150 mm(`GAP_REPAIR`) 안이면 같은 node로 합쳐 작도 오차로 끊긴 선을 이음
4. tick 중점을 10 mm 안의 가장 가까운 edge에 투영 → edge별 절단 위치 목록 (edge 시작점부터의 호길이 $s$, mm)
5. node마다 통과 쌍을 정함. 연결 2개면 그대로 통과, 3개 이상이면 서로 가장 반대 방향인 두 edge를 통과 쌍으로, 나머지를 분기로 둠
6. 통과 쌍을 따라 edge를 이어 chain을 만듦. chain = (이어 붙인 polyline, 절단 위치 목록, chain 위 node 위치 목록)

**(3) chain → 구간** (`make_sections()`, `:320`)

chain polyline을 절단 위치에서 잘라 구간 polyline 목록을 만든다.

**(4) 구간 + 이름표 → 이름 붙은 구간** (`assign_tags()`, `:334`; `absorb_untagged()`, `:352`)

1. 이름표마다 가장 가까운 구간을 찾아 TAGNUMBER를 붙임
2. 이름표가 없는 짧은 조각은 같은 chain의 이웃 구간에 합침. chain 시작부 조각은 다음 구간에, 나머지는 앞 구간에 붙음

**(5) 구간 → 흐름 link** (`connectivity()`, `:383`)

1. 같은 chain 안 이웃 구간 쌍 → `inline` 연결 후보
2. chain 끝이 다른 chain의 node에 닿는 곳 → 접합 연결 후보
3. 흐름 방향: 같은 line에서 TAGNUMBER가 커지는 쪽을 하류로 정함. 구간마다 polyline 방향이 흐름과 같으면 +1, 반대면 −1
4. 접합 연결은 분기 chain 끝이 흐름의 출구면 `merge`(합류), 입구면 `divert`(분기)
5. 결과: link 목록 `(src 구간, dst 구간, type)`

**(6) 구간 → 구간 JSON 항목** (`main()`, `:471-505`)

- polyline을 흐름 방향으로 뒤집어 정렬
- 길이, 중앙점, 중앙점 접선 방향, 시작·끝점, 현 길이를 계산
- 회전각 `turn_deg`: 120 mm 이상인 첫 조각과 마지막 조각의 방향 차이. |회전각| > 5°면 `kind = "curve"`, 아니면 `"straight"`
- `width_mm`: `config.yaml`에서 line key(`"11xx"` 등)로 찾음

**(7) 사람이 정한 해석 규칙 적용** (`:516-561`)

- 측면 이송 link 추가: 유입 link가 없고 infeed line도 아닌 구간은, 구간 중앙점에서 같은 line의 가장 가까운 구간까지 거리가 `side_transfer_max_mm` 이하면 `side_transfer` link를 더함
- 교차 기록: 다른 line의 polyline 선분이 서로의 내부에서 만나면 `crossings`에 넣음. 흐름 link는 만들지 않음
- 상류 link가 없는 구간 → `sources`, 하류 link가 없는 구간 → `sinks`

**(8) IoT·제어반 → 구간에 연결** (`:563-599`)

- IoT: y, x 순으로 정렬해 `IOT001`부터 번호를 매기고 가장 가까운 구간 id를 붙임
- 제어반: 외곽 중심에서 가장 가까운 MTEXT를 이름으로, block 이름(`CP500x300` 등)에서 공칭 크기를, 외곽 중심에서 문 호 중심 평균으로 향하는 단위 vector를 문 방향으로 둠. 가장 가까운 구간 id도 붙임

**(9) 원점 계산과 저장**: 모든 구간 점의 최솟값을 `origin_mm`으로 두고 JSON으로 저장

#### 출력 데이터

`out/equipment.json`. key 구조는 코드(`:491-619`)에서 확인한 것이고, 값은 실행 결과가 아니다.

```python
import json
eq = json.load(open("draft/out/equipment.json"))
eq.keys()
# source, units("mm"), origin_mm([x, y]), notes(해석 가정), log(변환 기록),
# conveyors, links, crossings, sources, sinks, control_panels, iot

eq["conveyors"][0].keys()
# id("CV"+TAGNUMBER), tag(int), line("11xx" 등), kind("straight"|"curve"),
# length_mm, width_mm, center_mm, heading_deg, start_mm, end_mm,
# turn_deg(왼쪽 회전 +), chord_mm, polyline_mm([[x, y], ...] 흐름 방향 순)

eq["links"][0]       # {"src": 구간 id, "dst": 구간 id, "type": "inline"|"merge"|"divert"|"side_transfer"}
eq["crossings"][0]   # {"a": 구간 id, "b": 구간 id, "at_mm": [x, y], "type": "grade_separated"}
eq["iot"][0].keys()  # id, pos_mm, radius_mm, conveyor, dist_to_conveyor_mm, dxf_handle
eq["control_panels"][0].keys()
# id, block, layer, center_mm, footprint_mm, nominal_mm, door_dir, outline_mm,
# door_arcs_mm, label_dist_mm, nearest_conveyor, dist_to_conveyor_mm, dxf_handle
```

#### 특이사항

- 흐름 방향은 도면에 있는 정보가 아니라 TAGNUMBER 증가 순서 규칙으로 정한 값이다 (`:384` docstring).
- 회전각 계산에서 120 mm 미만 조각을 무시하므로, 16xx lane 중간의 100 mm 꺾임(README: 작도 오차)은 직선으로 분류된다 (`:481`).

### 3.2 s1_plot_plan.py (확인용)

`equipment.json`의 구간·link·제어반·IoT를 원본 DXF 선 위에 겹쳐 PNG로 그린다. 변환 (1)~(8)이 도면과 맞는지 눈으로 보는 용도다.

- `dxf_paths()` (`:38`): DXF의 모든 entity를 block까지 재귀 분해해 `(N, 2)` polyline 목록으로 바꿈. 이 함수는 `s2_render.py`도 씀
- `offset_outline()` (`:26`): 구간 중심선 polyline을 폭만큼 좌우로 밀어 외곽 다각형으로 바꿈
- 출력: `out/s1_plan_overall.png`, `s1_plan_zoom_loop.png`, `s1_plan_zoom_infeed.png`

## 4. equipment.json → factory.usda

### 4.1 s2_build_scene.py

#### 역할

평면 구간 데이터(mm, 2D)를 3D 장면의 prim(m, 3D)으로 바꾼다. 구간마다 conveyor 3D asset을 하나 놓고, asset의 크기·방향·높이를 구간 데이터에 맞게 변환한다.

> ※ 용어 설명
> - pxr: OpenUSD의 Python binding. `Usd`(stage), `UsdGeom`(형상·변환), `Sdf`(속성 type) module을 씀
> - stage: 열린 USD 장면 하나
> - reference: 다른 USD 파일의 prim 트리를 이 prim 아래로 가져오는 방식
> - instanceable: 같은 asset을 reference한 prim끼리 형상 데이터를 공유하도록 하는 표시
> - asset local frame: asset 파일 안에서 형상이 놓인 기준 좌표. 배치 변환은 이 기준을 구간 위치로 옮기는 계산임

#### 입력 데이터

- `out/equipment.json`의 `conveyors`, `links`, `crossings`, `control_panels`, `iot`, `origin_mm`
- `config.yaml`의 `assets.*`, `control_panel.*`, `iot.*`, `grade_separation.*`
- asset local frame (docstring `:3-6`, 단위 m, Z-up)
  - 직선 a05: x = 0에서 +X 방향 길이 2.0, 폭 1.15 (Y 중앙), roller 윗면 z = 0.769
  - 곡선 a02: 입구 (0, 0)에서 +X 방향, 오른쪽 90° 회전, 중심선 반지름 $R_0$ = 1.4955

#### 변환 과정

**(1) 접합 끝 자르기** (`junction_trims()`, `:161`)

`merge`·`divert` link로 다른 conveyor에 붙는 직선 구간은 접합 끝을 식 (2)만큼 잘라, 3D에서 상대 conveyor와 겹치지 않게 한다. merge는 분기 구간의 끝, divert는 시작을 자른다.

$$
t = \frac{w_{\mathrm{host}} / 2}{\max(\sin\phi,\ 0.2)} \tag{2}
$$

- $t$: 잘라낼 길이 (mm)
- $w_{\mathrm{host}}$: 합류·분기 대상 conveyor의 폭 (mm)
- $\phi$: 두 구간 진행 방향 사이 각 (rad)

결과: `{구간 id: {"start": mm, "end": mm}}`

**(2) 구간 높이 계산** (`elevations()`, `:235`)

`crossings`와 `links`로 구간마다 (시작 높이 offset, 끝 높이 offset) (m)을 만든다.

1. 교차 목록에서 `elevated_line`(16xx) 쪽 구간을 $\Delta z$ = `roller_top_m` − 0.769로 평평하게 올림
2. `inline` link를 따라 그 앞 `ramp_sections_before`개, 뒤 `ramp_sections_after`개 구간을 찾고, 수평 길이 비율로 0 ↔ $\Delta z$ 선형 경사를 나눠 줌
3. `side_transfer` link로 붙은 평행 lane은 짝 구간의 높이를 하류로 따라가며 복사

결과: `{구간 id: (z0, z1)}`

**(3) 구간 → 배치 변환** (`straight_xform()`, `:125`; `curve_xform()`, `:137`)

직선 구간:
- 가장 긴 조각 방향을 진행 방향, 이 방향으로 투영한 길이에서 trim을 뺀 값을 배치 길이로 씀
- 결과: 시작점(world m), 방향각, 길이 scale = 배치 길이 / 2.0, 폭 scale = 폭 / 1.15

곡선 구간:
- 회전각을 30° 배수 $\theta$로 맞춤
- 시작–끝 현 길이 $c$에서 식 (3)으로 반지름을 구함
- 오른쪽 회전은 시작점을 입구로, 왼쪽 회전은 끝점을 입구로 한 오른쪽 회전 asset으로 놓음
- 결과: 입구 위치, 방향각, $\theta$, $R$, 뒤집음 여부

$$
R = \frac{c}{2\sin(\theta/2)} \tag{3}
$$

- $R$: 곡선 중심선 반지름 (m)
- $c$: 구간 시작–끝 현 길이 (m)
- $\theta$: 30° 배수로 맞춘 회전각 (rad)

**(4) 배치 변환 → USD prim** (`place()`, `:307`; `main()`, `:363-405`)

- 직선: 직선 asset을 reference하고 translate·rotate·scale을 설정. 높이 offset이 다르면(경사) 경사각만큼 기울이고, 경사 길이만큼 길이 scale을 늘리고, roller 윗면 시작점이 맞도록 위치를 보정
- 90° 곡선: 곡선 asset을 x·y 균일 scale $R / R_0$로 놓음
- 그 밖 각도의 곡선: `make_proc_curve()` (`:57`)가 같은 frame 규약의 곡선판 mesh(상판, 안·밖 frame, 다리 2개)를 `out/scene/proc/curve_a<각>_r<반지름>_w<폭>.usda`로 만들고 그 파일을 reference
- 구간 JSON 값과 배치 값을 `cadtwin:` 접두어 속성으로 prim에 기록

**(5) 부가 형상 생성**

- 지지 기둥 (`:408`): 높이 offset이 있는 직선 구간을 따라 `support_spacing_m` 간격으로 양쪽 cube 기둥. 다른 line conveyor 위에 놓일 기둥은 건너뜀
- 제어반 (`:455`): 외곽 크기 × 높이의 cube(`Body`) + 문 방향 얇은 판(`DoorFace`). `clear_panel()` (`:201`)이 conveyor 폭 안에 겹친 제어반을 가장 가까운 중심선에서 멀어지는 방향으로 밀어 위치를 바꿈
- IoT (`:494`): 표시 구. 높인 구간의 sensor는 구간 높이만큼 올림
- 바닥 (`:514`): 전체 구간 범위보다 3 m 넓은 사각 mesh

#### 출력 데이터

`out/scene/factory.usda`. prim 경로와 속성 이름은 코드(`:342-521`)에서 확인한 것이다.

```text
/World                         cadtwin:source_dxf, origin_mm, note
  /Conveyors/L11xx … /L16xx    line별 Scope
    /CV1101                    Xform. cadtwin:id, tag, line, kind, length_mm, width_mm,
                               dxf_start_mm, dxf_end_mm, upstream, downstream, crosses, asset_ref
                               (직선) placed_length_m, trim_start_mm, trim_end_mm
                               (높인 직선) elev_start_m, elev_end_m, slope_deg
                               (곡선) curve_deg, curve_radius_m, placed_reversed, end_error_mm, placed_width_mm
      /Geom                    asset reference, instanceable
  /Supports/<구간 id>_S<k><L|R>  Cube. cadtwin:supports
  /ControlPanels/CPn           Xform. cadtwin:id, block, nominal_mm, height_mm, dxf_center_mm,
                               moved_mm, nearest_conveyor, dxf_handle
    /Body, /DoorFace           Cube
  /IoT/IOTnnn                  Sphere. cadtwin:id, conveyor, dxf_pos_mm, dxf_handle
  /Floor                       Mesh
```

`equipment.json`의 `upstream`/`downstream` link 정보가 prim 속성으로 옮겨져, 장면만 열어도 구간 연결을 읽을 수 있다.

#### 특이사항

- 곡선 asset은 x·y 균일 scale이라 폭을 따로 맞추지 못한다 (README "알려진 한계", `:400`).
- 접합 trim은 직선 구간에만 적용한다 (`:169`).

### 4.2 usd_render.py (공용 module)

USD 장면을 pyrender scene으로 바꿔 이미지 배열로 그리는 함수 모음이다.

> ※ 용어 설명
> - pyrender: OpenGL 기반 Python render library. EGL을 쓰면 화면 없이 GPU로 그림
> - gprim: USD의 기하 primitive (Mesh, Cube, Sphere 등)
> - prototype: instanceable prim들이 공유하는 원본 형상 트리
> - UsdPreviewSurface: USD 표준 재질 shader. `diffuseColor`에 상수 색이나 texture를 연결함

변환 과정:

1. `build_scene()` (`:410`): USD stage → pyrender scene
   - instance prim을 prototype별로 모아 prototype 형상은 한 번만 변환하고, instance별 변환 행렬을 poses로 붙임
   - 나머지 gprim은 world 좌표 삼각형으로 변환
   - 같은 재질끼리 하나의 mesh로 합침 (`_merge()`, `:389`)
2. `_mesh_arrays()` (`:285`): USD Mesh(다각형 면 + 꼭짓점 index) → 꼭짓점별 삼각형 배열(위치, 법선, UV)
3. `_material()` (`:254`): UsdPreviewSurface의 texture 또는 상수 색 → pyrender 재질. 없으면 displayColor, 그것도 없으면 회색
4. `render()` (`:450`): camera 위치·주시점(world m) → `(높이, 너비, 3)` RGB 배열. 원근 또는 직교 투영

### 4.3 s2_render.py (확인용)

`factory.usda`를 `usd_render`로 그려 PNG로 저장한다. 위에서 본 직교 render 위에 `dxf_paths()`의 DXF 선을 식 (1)로 바꿔 빨간색으로 겹쳐, 3D 배치가 원본 도면과 맞는지 본다. 비스듬한 3D 시점 3개(`VIEWS`, `:169`)도 그린다. `VIEWS`는 `s4c_color_scene.py`가 같은 시점으로 다시 쓴다.

- 사용법: `python s2_render.py [scene.usda] [출력 접두어]` (기본 `out/scene/factory.usda`, `s2`)

## 5. equipment.json → simulation 결과 → factory_sim.usda

### 5.1 s4a_build_sim_model.py

#### 역할

`equipment.json`에서 simulation에 필요한 것(구간 길이와 흐름 연결)만 남겨 방향 graph로 바꾸고, 구간마다 흐름상 역할을 붙인다.

> ※ 용어 설명
> - networkx: Python graph library. 방향 graph(`DiGraph`), 경로 검사, 순환 탐색, 최단 경로 계산에 씀

#### 변환 과정

1. `conveyors` → node (속성 `line`, `length_m` = `length_mm` / 1000, `kind`), `links` → edge (속성 `type`)
2. 들어오고 나가는 edge의 type으로 node role을 정함. 표 4의 규칙을 위에서부터 적용해 처음 맞는 것을 씀 (`:59-75`)
3. 검증 정보 추가: 어느 source에서도 도달할 수 없는 sink 목록, 순환 개수와 길이
4. `sim_config.yaml`이 없으면 source·sink id를 채운 설정 template을 씀

**표 4. node role 판정 규칙 (위에서부터 적용)**

| 순서 | 조건 | role |
|---|---|---|
| 1 | 들어오는 edge 없음 | `source` |
| 2 | 나가는 edge 없음 | `sink` |
| 3 | 나가는 edge 중 `divert` 있음 | `divert` |
| 4 | 나가는 edge 중 `side_transfer` 있음 | `transfer` |
| 5 | 들어오는 edge 중 `merge` 있음 | `merge` |
| 6 | 그 밖 | `conveyor` |

#### 출력 데이터

```python
m = json.load(open("draft/out/sim_model.json"))
m.keys()           # nodes, edges, sources, sinks, crossings, checks
m["nodes"][0]      # {"id", "line", "length_m", "kind", "role"}
m["edges"][0]      # {"src", "dst", "type"}
m["checks"]        # {"unreachable_sinks": [...], "n_cycles": int, "cycle_lengths": [...]}
```

### 5.2 s4b_run_sim.py

#### 역할

graph(`sim_model.json`)와 설정(`sim_config.yaml`)을 SimPy model로 바꿔 실행하고, 물품 이동 기록을 구간별 통계로 바꾼다.

> ※ 용어 설명
> - SimPy: Python 이산 사건(discrete-event) simulation library. 물품 하나를 process(generator 함수)로, 한정된 자리를 resource로 표현함
> - 축적(accumulation) 구간: 앞이 막히면 물품이 멈춰 줄지어 쌓이는 conveyor 구간
> - blocking: 다음 구간에 자리가 날 때까지 현재 구간 자리를 붙잡고 기다리는 것. 정체가 상류로 전파됨
> - headway: 같은 입구로 연속한 두 물품이 들어가는 최소 시간 간격
> - Dijkstra 알고리즘: 가중치 있는 graph에서 한 node로부터 모든 node까지 최단 거리를 구하는 알고리즘

#### 변환 과정

**(1) graph node → `Section` 객체** (`:143`)

node 하나의 길이와 설정값으로 구간의 자리 수와 시간을 정한다 (식 (4)~(6)).

$$
\mathrm{cap} = \max\!\left(1,\ \left\lceil \frac{L}{p} \right\rceil\right) \tag{4}
$$

$$
T_{\mathrm{transit}} = \frac{L}{v} \tag{5}
$$

$$
h = \frac{p}{v} \tag{6}
$$

- $\mathrm{cap}$: 구간에 동시에 있을 수 있는 물품 수 (개)
- $L$: 구간 길이 (m)
- $p$: pitch (m)
- $T_{\mathrm{transit}}$: 물품이 구간을 지나는 시간 (s)
- $v$: 구간 line의 속도 (m/s)
- $h$: headway (s)

구간마다 SimPy resource 두 개를 둔다. `slots`(용량 cap)는 구간 위 자리이고, `gate`(용량 1)는 입구로 물품이 들어간 뒤 $h$초 동안 붙잡힌다.

**(2) graph → 경로표** (`Model.__init__()`, `:192`)

edge 가중치를 도착 구간 길이로 두고, sink마다 역방향 graph에서 Dijkstra를 돌려 `dist[sink][node]` = node에서 sink까지 거리를 만든다. 물품은 이동할 때마다 이 표로 다음 구간 후보를 정렬한다 (`candidates()`, `:224`).

**(3) 설정 → 물품 생성** (`source()`, `:299`)

source 구간마다 `rate_per_h`로 지수분포 간격을 뽑아 물품을 만들고, sink `weight` 비율로 목적지를 뽑는다.

**(4) 물품 이동** (`item()`, `:238`)

물품 하나는 아래 반복으로 구간을 옮겨 간다.

1. 현재 구간에서 $T_{\mathrm{transit}}$만큼 진행
2. 현재 구간이 목적지면 출구 처리자(용량 1 resource)를 얻어 `service_s`만큼 처리하고 끝
3. 아니면 목적지까지 가까운 다음 구간 후보를 고름. `recirculate_if_blocked`가 켜져 있고 첫 후보가 꽉 찼으며 그 edge가 `divert`·`side_transfer`면 두 번째 후보(loop 계속)로 감
4. 다음 구간의 slot과 gate를 얻을 때까지 현재 slot을 놓지 않고 기다림 (blocking). 기다린 시간을 현재 구간의 대기로 누적
5. 분기·측면 이송이면 추가 시간만큼 더 머문 뒤 다음 구간으로 옮김

이동할 때마다 구간의 점유 수와 시간 적분, 대기 시간 합이 쌓인다 (`entered()`, `left()`, `_acc()`).

**(5) 이동 기록 → 구간별 통계** (`main()`, `:340-376`)

warm-up 시간(`warmup()`, `:308`)이 지나면 통계를 초기화하고 그 뒤만 집계한다. 실행이 끝나면 누적값을 구간별 지표로 바꾼다.

- `throughput_per_h`: 나간 물품 수 / 집계 시간 × 3600
- `avg_wait_s`: 다음 구간 진입 대기 합 / 횟수
- `utilization`: 점유 수의 시간 평균 / cap
- `picker_utilization` (sink만): 출구 처리 시간 합 / 집계 시간

이 지표로 정체와 병목을 표 5의 기준으로 판정해 구간에 표시한다.

**표 5. 정체·병목 판정 기준 (`:351-360`)**

| 판정 | 조건 |
|---|---|
| `congested` | `avg_wait_s` ≥ 5.0 s |
| `bottleneck` | 상류 구간이 있고, 자신의 `avg_wait_s` < 1.0 s, `utilization` > 0.5, 상류 중 최대 `avg_wait_s` > 5.0 s |
| `bottleneck` (sink) | `picker_utilization` ≥ 0.85 |

#### 출력 데이터

```python
r = json.load(open("draft/out/sim_results.json"))
r.keys()              # summary(전체 집계), sections(구간별 행), config(사용한 sim_config 내용)
r["sections"][0].keys()
# id, line, role, length_m, capacity, speed_mps, throughput_per_h, avg_wait_s,
# utilization, max_occupancy, congested, bottleneck, picker_utilization(sink만)
```

`sim_results.csv`는 `sections`를 한 행씩 쓴 표다.

#### 특이사항

- model의 단순화 범위는 README "알려진 한계"에 적혀 있다: 구간 = 용량 ceil(L/pitch)의 축적 구간, blocking 전파, 합류 선착순. 가감속·물품 크기 차이·제어 logic은 없음.

### 5.3 s4c_color_scene.py

#### 역할

구간별 통계(`sim_results.json`)를 3D 장면 데이터로 옮긴다. 원래 장면 파일은 고치지 않고, 그 위에 겹치는 새 layer 파일에 통계 속성과 색 형상을 쓴다.

> ※ 용어 설명
> - sublayer: 한 USD layer가 다른 layer 파일을 아래층으로 포함하는 방식. 위층 값이 아래층 값을 덮어씀
> - over: 아래층에 이미 있는 prim에 속성만 덧붙이는 prim 명세
> - UsdLux: USD 조명 schema
> - customLayerData: USD layer 머리의 임의 dict. Omniverse(NVIDIA의 USD 기반 3D platform)가 여기서 시작 camera 설정을 읽음

#### 입력 데이터

- `out/equipment.json` (구간 polyline, 폭)
- `out/sim_results.json` (구간별 통계)
- `out/scene/factory.usda` (구간 prim 경로)
- `s2_build_scene`의 trim·높이 함수

#### 변환 과정

1. 구간 중심선 → world 좌표 polyline (`section_polys()`, `:81`): 직선은 접합 trim을 반영해 잘라냄
2. 중심선 → 띠 (`ribbon()`, `:57`): polyline 각 점에서 접선의 법선 방향으로 폭의 절반씩 밀어 좌우 경계선 두 줄을 만듦
3. 통계 → 색 (`color_of()`, `:70`; `norm_for()`, `:90`): 점유율은 0–1, 나머지 지표는 0 ~ 99 percentile로 정규화한 뒤 color map에 넣음. 병목·정체 구간은 빨강으로 고정
4. `factory_sim.usda` 작성 (`build_usd()`, `:97`)
   - `./factory.usda`를 sublayer로 둠
   - 구간 prim마다 over로 `sim:throughput_per_h`, `sim:avg_wait_s`, `sim:utilization`, `sim:capacity`, `sim:bottleneck`, `sim:congested` 속성을 씀
   - 띠 경계선 두 줄을 사각 면으로 이어 `/World/SimOverlay/<구간 id>` mesh를 만듦. 높이는 0.82 m + 구간 높이 offset이라 경사로를 따라감. 색은 점유율(`COLOR_METRIC`)
   - 병목 구간에 빨간 기둥·깃발 cube 추가
   - Omniverse viewer용 조명과 시작 camera를 layer에 기록
5. 확인 이미지: 지표 3개의 2D 지도(`metric_maps()`, `:199`), `usd_render`로 그린 top view와 3D view

#### 출력 데이터

```text
factory_sim.usda
  subLayers = ["./factory.usda"]
  over /World/Conveyors/L??xx/CV????     sim:throughput_per_h, sim:avg_wait_s, sim:utilization,
                                         sim:capacity, sim:bottleneck, sim:congested
  /World/SimOverlay/CV????               Mesh 색 띠. sim:metric, sim:value
  /World/SimOverlay/CV????_bottleneckPost, _bottleneckFlag   (병목 구간만)
  /World/Lights/Dome, /World/Lights/Sun
```

이 파일을 열면 `factory.usda`의 `cadtwin:*` 속성과 이 layer의 `sim:*` 속성이 같은 구간 prim에 함께 보인다.

## 6. factory_sim.usda → USDZ

### 6.1 s5_export_usdz.py

#### 역할

`factory_sim.usda`(여러 파일 reference, sublayer, 암시적 도형, MDL 재질 포함)를 Quick Look이 읽을 수 있는 단일 USDZ package로 바꾼다.

> ※ 용어 설명
> - Quick Look: macOS·iOS 파일 미리보기 기능. USDZ를 RealityKit으로 그림
> - MDL (Material Definition Language): NVIDIA 재질 기술 언어. asset 재질에 쓰이며 Quick Look은 읽지 못함
> - ARKit compliance checker: OpenUSD에 포함된 USDZ 호환성 검사 도구

#### 변환 과정

1. 좌표계 변환 (`export()`, `:507`): 새 stage를 Y-up으로 만들고 root `/Root`를 X축 −90° 회전해 Z-up 데이터를 그대로 담음
2. 형상 변환 (`gprim_arrays()`, `:328`; `emit_mesh()`, `:436`): Cube·Sphere는 꼭짓점·면 배열로 풀어 Mesh로, 모든 형상은 world 변환 행렬을 꼭짓점에 곱해 world 좌표로 바꿈
3. 재질 변환 (`Materials`, `:364`): 원본의 UsdPreviewSurface texture·상수 색 또는 displayColor → 새 UsdPreviewSurface 재질. texture는 긴 변 1,024 px JPEG로 줄임. 같은 색·texture는 재질 하나로 공유
4. conveyor 처리는 mode에 따라 다름
   - `full`: 원본 prototype마다 class prim `/Proto_<n>`에 mesh를 한 번 복사하고, 구간마다 world 변환 행렬 + internal reference(instanceable)로 둠
   - `lite` (`lite_conveyors()`, `:465`): asset 대신 `s4c_color_scene`의 띠 형상을 두께 0.15 m 판으로 만들고 다리를 붙임
5. 나머지 형상(색 띠, 병목 표시, 제어반, 지지 기둥, IoT, 바닥) → `/Root/Static/` 아래 world 좌표 mesh
6. `UsdUtils.CreateNewUsdzPackage`로 `.usdc`와 texture를 USDZ로 묶고, ARKit 검사를 돌림

- 사용법: `python s5_export_usdz.py [full|lite|both]` (기본 `both`)
- 출력: `out/factory_sim_full.usdz`, `out/factory_sim_lite.usdz`

#### 특이사항

- 형상과 재질만 옮긴다. `emit_mesh()`는 `cadtwin:*`, `sim:*` 속성을 쓰지 않으므로 USDZ에는 구간 속성과 통계 속성이 없다.
- 복사 대상이 Mesh·Cube·Sphere뿐이라 `factory_sim.usda`의 조명과 camera 설정은 USDZ에 들어가지 않는다 (`:540`, `:568`).
