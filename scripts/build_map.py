#!/usr/bin/env python3
"""지도 파일(data/geo/metro.json) 생성기: 전국 시도 + 수도권 시군구 — 행정구역이 바뀔 때만 로컬에서 한 번 실행.

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
import shapely.affinity
from shapely.geometry import shape, MultiPolygon, Polygon
from shapely.ops import polylabel

OUT = Path(__file__).resolve().parent.parent / "data" / "geo" / "metro.json"
SIDO = {"서울특별시": "서울", "경기도": "경기", "인천광역시": "인천"}
# 시도 이름 → R-ONE 주간 통계 이름
SIDO_ALL = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주",
}
GWANGJU_GU = {"동구", "서구", "남구", "북구", "광산구"}  # 통합특별시 안에서 옛 광주광역시 지역


def sido_short(p):
    if p["sidonm"] == "전남광주통합특별시":  # 2026년 통합. R-ONE 비교를 위해 옛 광주/전남으로 나눠 둔다
        return "광주" if p["sggnm"] in GWANGJU_GU else "전남"
    return SIDO_ALL[p["sidonm"]]
SKIP = {"옹진군", "울릉군"}  # 먼 섬들이라 지도 범위를 크게 벌려 제외
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


def to_path(geom, proj, min_area=0):
    parts = []
    for poly in polygons(geom):
        if poly.area < min_area:  # 전국 지도에서 아주 작은 섬은 생략
            continue
        for ring in [poly.exterior, *poly.interiors]:
            pts = [proj(x, y) for x, y in ring.coords[:-1]]
            if len(pts) < 3:
                continue
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    return "".join(parts)


def build(regions, tolerance, context=None, min_area=0):
    keys = list(regions)
    simplified = shapely.coverage_simplify([regions[k] for k in keys], tolerance)
    proj, height = project_factory(simplified)
    out = []
    for key, geom in zip(keys, simplified):
        biggest = max(polygons(regions[key]), key=lambda p: p.area)
        label = polylabel(biggest, tolerance=0.0005)
        lx, ly = proj(label.x, label.y)
        sido, name = key.split("|")
        out.append({"key": key, "sido": sido, "name": name, "d": to_path(geom, proj, min_area),
                    "lx": round(lx, 1), "ly": round(ly, 1)})
    result = {"viewBox": f"0 0 {WIDTH} {height}", "regions": out}
    if context is not None:
        result["context"] = to_path(shapely.simplify(context, tolerance), proj)
    return result


def main():
    src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    parts, all_sgg = defaultdict(list), defaultdict(list)
    for f in src["features"]:
        p = f["properties"]
        if p["sggnm"] in SKIP:
            continue
        geom = shape(f["geometry"]).buffer(0)
        all_sgg[(sido_short(p), p["sggnm"])].append(geom)
        if p["sidonm"] in SIDO:
            parts[f"{SIDO[p['sidonm']]}|{p['sggnm'].replace(' ', '')}"].append(geom)
    dissolved = {k: shapely.union_all(v) for k, v in parts.items()}
    # 전국 시도: 시군구 단위로 먼저 단순화해 경계가 서로 맞게 한 뒤 시도로 합친다.
    # 키는 "전국|서울" 처럼 쓰고, 이름은 R-ONE 시도 이름과 같다
    keys = list(all_sgg)
    simple = shapely.coverage_simplify([shapely.union_all(all_sgg[k]) for k in keys], 0.01)
    by_sido = defaultdict(list)
    for (sido, _), g in zip(keys, simple):
        by_sido[sido].append(g)
    nation = {f"전국|{k}": shapely.union_all(v).buffer(0.002).buffer(-0.002) for k, v in by_sido.items()}
    # 제주는 휴대폰 화면 세로 공간을 아끼려고 실제보다 북쪽(본토 가까이)으로 옮겨 그린다
    nation["전국|제주"] = shapely.affinity.translate(nation["전국|제주"], xoff=0.3, yoff=0.55)

    seoul = {k: g for k, g in dissolved.items() if k.startswith("서울|")}
    rest = {k: g for k, g in dissolved.items() if not k.startswith("서울|")}
    data = {
        "source": "vuski/admdongkor ver20261001 (통계청 행정구역 경계)",
        "nation": build(nation, 0.004, min_area=0.004),
        "seoul": build(seoul, 0.0006),
        "metro": build(rest, 0.0015, context=shapely.union_all(list(seoul.values()))),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT} — 전국 {len(nation)}개 시도, 서울 {len(seoul)}개, 경기·인천 {len(rest)}개, "
          f"{OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
