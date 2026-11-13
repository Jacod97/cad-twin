import os 
import collections
import ezdxf

dxf_dir = "data/dxf"
file = os.listdir(dxf_dir)[0]
file_path = os.path.join(dxf_dir, file)

UNITS = {0: "미지정", 1: "inch", 2: "feet", 4: "mm", 5: "cm", 6: "m"}

doc = ezdxf.readfile(file_path)
msp = doc.modelspace()
# cnt = collections.Counter(e.dxf.name for e in msp.query("INSERT"))

# print("파일:", file_path)
# print("단위:", UNITS.get(doc.header.get("$INSUNITS", 0), "기타"))
# print("블록 배치 총", sum(cnt.values()), "개 / 종류", len(cnt), "개")
# print("--- 많이 쓰인 블록 상위 30개 ---")
# for name, n in cnt.most_common(30):
#     print(f"{n:5d}  {name}")


# cnt = collections.Counter((e.dxf.layer, e.dxftype()) for e in msp)

# print("레이어 수:", len(doc.layers))
# print("--- 레이어별 엔티티 (많은 순 상위 40개) ---")
# for (layer, etype), n in cnt.most_common(40):
#     print(f"{n:6d}  {etype:12s}  {layer}")

print("=== 1. 컨베이어 이름표 (처음 10개) ===")
for e in list(msp.query('INSERT[name=="name"]'))[:10]:
    attrs = {a.dxf.tag: a.dxf.text for a in e.attribs}
    x, y = e.dxf.insert.x, e.dxf.insert.y
    print(f"위치=({x:.0f}, {y:.0f})  속성={attrs}")

print("\n=== 2. 0 레이어 블록 안에 든 엔티티 수 ===")
for e in msp.query('INSERT[layer=="0"]'):
    blk = doc.blocks.get(e.dxf.name)
    kinds = {}
    for s in blk:
        kinds[s.dxftype()] = kinds.get(s.dxftype(), 0) + 1
    print(f"{e.dxf.name:12s} 내부: {kinds}")

print("\n=== 3. 0 레이어 선 길이 분포 ===")
lens = sorted(e.dxf.start.distance(e.dxf.end) for e in msp.query('LINE[layer=="0"]'))
print("개수:", len(lens))
print("가장 짧은 5개(mm):", [round(v) for v in lens[:5]])
print("가장 긴 5개(mm):", [round(v) for v in lens[-5:]])