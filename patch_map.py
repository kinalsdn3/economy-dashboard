#!/usr/bin/env python3
"""지도 파일(data/geo/metro.json) 다듬기 — build_map.py 다음에 로컬에서 한 번 실행.

1) 서울 지도에 한강 선(river)을 넣는다: 강북 쪽 구와 강남 쪽 구가 맞닿는 경계가 곧 한강 한가운데다.
2) 경기·인천 지도에서 인천을 빼고, 경기만 꽉 차게 다시 맞춘다.

필요: pip install shapely  (GitHub Actions 에서는 쓰지 않음)
사용: python scripts/patch_map.py
"""
import json
import re
from pathlib import Path

import shapely
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

GEO = Path(__file__).resolve().parent.parent / "data" / "geo" / "metro.json"
NORTH = {"마포구", "용산구", "성동구", "광진구"}
SOUTH = {"강서구", "영등포구", "동작구", "서초구", "강남구", "송파구", "강동구"}
NUM = re.compile(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
WIDTH = 1000


def rings(d):
    out = []
    for part in d.split("M")[1:]:
        pts = [(float(x), float(y)) for x, y in NUM.findall(part)]
        if len(pts) >= 3:
            out.append(LineString(pts + [pts[0]]))
    return out


def line_path(geom):
    lines = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return "".join("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in ln.coords) for ln in lines if ln.length > 0)


def add_river(seoul):
    by = {r["name"]: r for r in seoul["regions"]}
    north = shapely.union_all([ln for n in NORTH for ln in rings(by[n]["d"])])
    south = shapely.union_all([ln for n in SOUTH for ln in rings(by[n]["d"])])
    shared = north.intersection(south)
    segs = [g for g in getattr(shared, "geoms", [shared]) if isinstance(g, LineString) and g.length > 0]
    # 강서구 북쪽(고양 맞은편)·강동구 북쪽(구리 맞은편)도 한강이다: 서울 바깥 경계를 따라
    # 강 끝점에서 강서는 가장 북서쪽 끝, 강동은 가장 북동쪽 끝까지 잇는다
    for name, far in (("강서구", lambda p: p[0] + p[1]), ("강동구", lambda p: -p[0] + p[1])):
        others = shapely.union_all([ln for r in seoul["regions"] if r["name"] != name for ln in rings(r["d"])])
        outer = shapely.union_all(rings(by[name]["d"])).difference(others)
        if not isinstance(outer, LineString):
            outer = linemerge(outer)
        outer = max(getattr(outer, "geoms", [outer]), key=lambda g: g.length)
        ends = [p for s in segs for p in (s.coords[0], s.coords[-1])]
        pts = list(outer.coords)
        dist = lambda p: min((p[0] - e[0]) ** 2 + (p[1] - e[1]) ** 2 for e in ends)
        if dist(pts[-1]) < dist(pts[0]):
            pts.reverse()
        k = min(range(len(pts)), key=lambda i: far(pts[i]))
        if k > 0:
            segs.append(LineString(pts[:k + 1]))
    river = linemerge(MultiLineString(segs))
    seoul["river"] = line_path(river)
    return river


def drop_incheon(metro):
    regions = [r for r in metro["regions"] if r["sido"] != "인천"]
    xs, ys = [], []
    for r in regions:
        for x, y in NUM.findall(r["d"]):
            xs.append(float(x)); ys.append(float(y))
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    s = WIDTH / (maxx - minx)

    def tf(d):
        return NUM.sub(lambda m: f"{(float(m[1]) - minx) * s:.1f},{(float(m[2]) - miny) * s:.1f}", d)
    for r in regions:
        r["d"] = tf(r["d"])
        r["lx"] = round((r["lx"] - minx) * s, 1)
        r["ly"] = round((r["ly"] - miny) * s, 1)
    metro["regions"] = regions
    if metro.get("context"):
        metro["context"] = tf(metro["context"])
    metro["viewBox"] = f"0 0 {WIDTH} {round((maxy - miny) * s)}"


def main():
    data = json.loads(GEO.read_text(encoding="utf-8"))
    river = add_river(data["seoul"])
    if any(r["sido"] == "인천" for r in data["metro"]["regions"]):
        drop_incheon(data["metro"])
    GEO.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"한강 {len(getattr(river, 'geoms', [river]))}조각 · 경기 {len(data['metro']['regions'])}개 · "
          f"viewBox {data['metro']['viewBox']} · {GEO.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
