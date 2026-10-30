#!/usr/bin/env python3
"""수도권 시군구 지도 파일(data/geo/metro.json) 생성기 — 행정구역이 바뀔 때만 로컬에서 한 번 실행.

입력: vuski/admdongkor 의 행정동 GeoJSON (예: HangJeongDong_ver20261001.geojson)
  https://github.com/vuski/admdongkor
필요: pip install shapely  (GitHub Actions 에서는 쓰지 않음)

사용: python scripts/build_map.py <행정동.geojson>
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import shapely
from shapely.geometry import shape, MultiPolygon, Polygon
from shapely.ops import polylabel

OUT = Path(__file__).resolve().parent.parent / "data" / "geo" / "metro.json"
SIDO = {"서울특별시": "서울", "경기도": "경기", "인천광역시": "인천"}
SKIP = {"옹진군"}  # 서해 먼 섬들이라 지도 범위를 크게 벌려 제외
WIDTH = 1000


def polygons(geom):
    return list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]


def project_factory(geoms):
    minx, miny, maxx, maxy = shapely.union_all(geoms).bounds
    k = math.cos(math.radians((miny + maxy) / 2))
    scale = WIDTH / ((maxx - minx) * k)
    height = (maxy - miny) * scale

    def proj(x, y):
        return (x - minx) * k * scale, (maxy - y) * scale
    return proj, round(height)


def to_path(geom, proj):
    parts = []
    for poly in polygons(geom):
        for ring in [poly.exterior, *poly.interiors]:
            pts = [proj(x, y) for x, y in ring.coords[:-1]]
            if len(pts) < 3:
                continue
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    return "".join(parts)


def build(regions, tolerance, context=None):
    keys = list(regions)
    simplified = shapely.coverage_simplify([regions[k] for k in keys], tolerance)
    proj, height = project_factory(simplified)
    out = []
    for key, geom in zip(keys, simplified):
        biggest = max(polygons(regions[key]), key=lambda p: p.area)
        label = polylabel(biggest, tolerance=0.0005)
        lx, ly = proj(label.x, label.y)
        sido, name = key.split("|")
        out.append({"key": key, "sido": sido, "name": name, "d": to_path(geom, proj),
                    "lx": round(lx, 1), "ly": round(ly, 1)})
    result = {"viewBox": f"0 0 {WIDTH} {height}", "regions": out}
    if context is not None:
        result["context"] = to_path(shapely.simplify(context, tolerance), proj)
    return result


def main():
    src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    parts = defaultdict(list)
    for f in src["features"]:
        p = f["properties"]
        if p["sidonm"] in SIDO and p["sggnm"] not in SKIP:
            parts[f"{SIDO[p['sidonm']]}|{p['sggnm'].replace(' ', '')}"].append(shape(f["geometry"]).buffer(0))
    dissolved = {k: shapely.union_all(v) for k, v in parts.items()}

    seoul = {k: g for k, g in dissolved.items() if k.startswith("서울|")}
    rest = {k: g for k, g in dissolved.items() if not k.startswith("서울|")}
    data = {
        "source": "vuski/admdongkor ver20261001 (통계청 행정구역 경계)",
        "seoul": build(seoul, 0.0006),
        "metro": build(rest, 0.0015, context=shapely.union_all(list(seoul.values()))),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT} — 서울 {len(seoul)}개, 경기·인천 {len(rest)}개, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
