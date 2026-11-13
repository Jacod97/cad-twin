from pxr import Usd, UsdGeom

FILE_PATH = "assets/conveyor_belt_a47/simready_usd/sm_conveyor_belt_a47_01.usd"

stage = Usd.Stage.Open(FILE_PATH)
print("단위(metersPerUnit):", UsdGeom.GetStageMetersPerUnit(stage))
print("위쪽 축(upAxis):", UsdGeom.GetStageUpAxis(stage))

root = stage.GetDefaultPrim()
print("기본 prim:", root.GetPath())

bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
rng = bbox.ComputeWorldBound(root).ComputeAlignedRange()
mn, mx = rng.GetMin(), rng.GetMax()
size = mx - mn
print("최소:", [round(v, 3) for v in mn])
print("최대:", [round(v, 3) for v in mx])
print("크기(x, y, z):", [round(v, 3) for v in size])