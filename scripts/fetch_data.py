#!/usr/bin/env python3
"""경제지표 대시보드 데이터 수집기.

GitHub Actions 에서 하루 한 번 실행되어 data/data.json 을 갱신한다.
표준 라이브러리만 사용한다.

환경변수(GitHub Secrets):
  ECOS_API_KEY  한국은행 ECOS
  FRED_API_KEY  미국 세인트루이스 연준 FRED
  REB_API_KEY   한국부동산원 R-ONE
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "data.json"
KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()

ECOS_KEY = os.environ.get("ECOS_API_KEY", "").strip()
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
REB_KEY = os.environ.get("REB_API_KEY", "").strip()

# R-ONE 통계표 ID
REB_WEEKLY_SALE = "T244183132827305"    # (주) 매매가격지수 - 주간아파트동향
REB_WEEKLY_JEONSE = "T247713133046872"  # (주) 전세가격지수 - 주간아파트동향
REB_MONTHLY_TRADE = "A_2024_00554"      # (월) 행정구역별 아파트매매거래현황
REB_NATIONWIDE = 50001                  # 주간 통계의 '전국' 분류 ID
REB_SEOUL_TRADE = 500002                # 거래현황 통계의 '서울' 분류 ID
METRO = ("서울", "경기", "인천")


def get_json(url, params=None, tries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (econ-dashboard)"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def days_ago(n):
    return TODAY - dt.timedelta(days=n)


def week_id(d):
    """R-ONE 주간 시점 ID (ISO 연도 + 주차, 예: 202644)."""
    y, w, _ = d.isocalendar()
    return f"{y}{w:02d}"


Y1 = days_ago(370)
Y3 = days_ago(3 * 365 + 31)


# ---------- 소스별 수집: 모두 [[날짜, 값], ...] 을 날짜 오름차순으로 반환 ----------

def yahoo(symbol, rng="1y"):
    d = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}",
                 {"range": rng, "interval": "1d"})
    res = d["chart"]["result"][0]
    offset = res["meta"].get("gmtoffset", 0)
    points = {}
    for ts, close in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if close is None:
            continue
        day = dt.datetime.fromtimestamp(ts + offset, dt.timezone.utc).date().isoformat()
        points[day] = round(close, 4)
    # 마지막 거래일 일봉 종가가 비어 오는 경우가 있어 meta 의 최신가로 보완
    meta = res["meta"]
    if meta.get("regularMarketTime") and meta.get("regularMarketPrice"):
        day = dt.datetime.fromtimestamp(meta["regularMarketTime"] + offset, dt.timezone.utc).date().isoformat()
        points.setdefault(day, round(meta["regularMarketPrice"], 4))
    return [[k, v] for k, v in sorted(points.items())]


def ecos(stat, cycle, item, start):
    if not ECOS_KEY:
        raise RuntimeError("ECOS_API_KEY 없음")
    fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
    d = get_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/10000/"
                 f"{stat}/{cycle}/{start.strftime(fmt)}/{TODAY.strftime(fmt)}/{item}")
    if "StatisticSearch" not in d:
        raise RuntimeError(f"ECOS {stat}/{item}: {d.get('RESULT', d)}")
    out = []
    for r in d["StatisticSearch"]["row"]:
        if r["DATA_VALUE"] in ("", None):
            continue
        t = r["TIME"]
        day = f"{t[:4]}-{t[4:6]}-{t[6:]}" if cycle == "D" else f"{t[:4]}-{t[4:6]}"
        out.append([day, float(r["DATA_VALUE"])])
    return out


def fred(series_id, start, units="lin", monthly=False):
    if not FRED_KEY:
        raise RuntimeError("FRED_API_KEY 없음")
    d = get_json("https://api.stlouisfed.org/fred/series/observations", {
        "series_id": series_id, "api_key": FRED_KEY, "file_type": "json",
        "observation_start": start.isoformat(), "units": units,
    })
    out = []
    for o in d["observations"]:
        if o["value"] in (".", ""):
            continue
        out.append([o["date"][:7] if monthly else o["date"], round(float(o["value"]), 4)])
    return out


def reb(statbl, cycle, **filters):
    """R-ONE 통계표 데이터 전체 페이지 조회."""
    if not REB_KEY:
        raise RuntimeError("REB_API_KEY 없음")
    rows, page = [], 1
    while page <= 40:
        d = get_json("https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do", {
            "KEY": REB_KEY, "Type": "json", "STATBL_ID": statbl, "DTACYCLE_CD": cycle,
            "pIndex": page, "pSize": 1000, **filters,
        })
        body = d.get("SttsApiTblData")
        result = (d.get("RESULT") or {}) if not body else next(
            (h["RESULT"] for h in body[0]["head"] if "RESULT" in h), {})
        code = result.get("CODE", "")
        if code == "INFO-200":  # 해당 데이터 없음
            break
        if code != "INFO-000" or len(body) < 2:
            raise RuntimeError(f"R-ONE {statbl}: {result or d}")
        total = body[0]["head"][0]["list_total_count"]
        chunk = body[1]["row"]
        rows += chunk
        if not chunk or len(rows) >= total:
            break
        page += 1
    return rows


def naver_flows(n=20):
    """코스피 투자자별 순매수(억원), 최근 n 거래일."""
    rows, d = [], TODAY
    for _ in range(45):
        if len(rows) >= n:
            break
        if d.weekday() < 5:
            ymd = d.strftime("%Y%m%d")
            j = get_json("https://m.stock.naver.com/api/index/KOSPI/trend", {"bizdate": ymd})
            vals = [int(str(j.get(k) or "0").replace(",", "").replace("+", ""))
                    for k in ("personalValue", "foreignValue", "institutionalValue")]
            if j.get("bizdate") == ymd and any(vals):
                rows.append({"date": d.isoformat(), "personal": vals[0],
                             "foreign": vals[1], "institutional": vals[2]})
        d -= dt.timedelta(days=1)
    if not rows:
        raise RuntimeError("네이버 투자자별 매매동향 응답 없음")
    return rows[::-1]


def ecos_foreign_flows():
    s = ecos("802Y001", "D", "0030000", days_ago(40))[-20:]
    return [{"date": day, "personal": None, "foreign": int(v), "institutional": None} for day, v in s]


# ---------- 부동산 가공 ----------

def weekly_national(statbl):
    rows = reb(statbl, "WK", CLS_ID=REB_NATIONWIDE, ITM_ID=10001,
               START_WRTTIME=week_id(days_ago(400)), END_WRTTIME=week_id(TODAY))
    by_week = {r["WRTTIME_IDTFR_ID"]: [r["WRTTIME_DESC"], round(r["DTA_VAL"], 4)] for r in rows}
    return [by_week[k] for k in sorted(by_week)]


def region_label(fullnm):
    # "서울>강북지역>도심권>종로구" → "종로구", "경기>경부1권>수원시>장안구" → "수원 장안구"
    parts = fullnm.split(">")
    leaf = parts[-1]
    if len(parts) >= 3 and parts[-2].endswith("시") and leaf.endswith("구"):
        return f"{parts[-2][:-1]} {leaf}"
    return leaf


def weekly_regions(statbl):
    """서울/경기/인천 시군구의 전주 대비 변동률 상승·하락 TOP5."""
    rows = reb(statbl, "WK", ITM_ID=10001,
               START_WRTTIME=week_id(days_ago(35)), END_WRTTIME=week_id(TODAY))
    weeks = sorted({r["WRTTIME_IDTFR_ID"] for r in rows})
    if len(weeks) < 2:
        raise RuntimeError("주간 지역 데이터 부족")
    cur, prev = weeks[-1], weeks[-2]
    val, desc = {}, {}
    for r in rows:
        val[(r["WRTTIME_IDTFR_ID"], r["CLS_FULLNM"])] = r["DTA_VAL"]
        desc[r["WRTTIME_IDTFR_ID"]] = r["WRTTIME_DESC"]
    names = {n for (w, n) in val if w == cur}

    def change(n):
        a, b = val.get((cur, n)), val.get((prev, n))
        return None if a is None or not b else (a / b - 1) * 100

    groups = []
    for top in METRO:
        leaves = [n for n in names
                  if n.startswith(top + ">") and not any(m.startswith(n + ">") for m in names)]
        items = [(region_label(n), c) for n in leaves if (c := change(n)) is not None]
        up = sorted((i for i in items if round(i[1], 2) > 0), key=lambda i: -i[1])[:5]
        down = sorted((i for i in items if round(i[1], 2) < 0), key=lambda i: i[1])[:5]
        total = change(top)
        groups.append({
            "name": top,
            "change": None if total is None else round(total, 3),
            "count": len(items),
            "up": [{"name": n, "change": round(c, 3)} for n, c in up],
            "down": [{"name": n, "change": round(c, 3)} for n, c in down],
        })
    return {"asof": desc[cur], "prev": desc[prev], "groups": groups}


def seoul_trades():
    rows = reb(REB_MONTHLY_TRADE, "MM", CLS_ID=REB_SEOUL_TRADE, ITM_ID=100001,
               START_WRTTIME=days_ago(800).strftime("%Y%m"), END_WRTTIME=TODAY.strftime("%Y%m"))
    by_month = {r["WRTTIME_IDTFR_ID"]: r["DTA_VAL"] for r in rows}
    return [[f"{k[:4]}-{k[4:]}", by_month[k]] for k in sorted(by_month)]


# ---------- 지표 정의 ----------
# (id, 이름, 단위, 주기 D/W/M, 변화 표시 pct/pp, 소수점, [(출처, 수집함수), ...]) — 앞 출처 실패 시 다음 출처 사용

SERIES = [
    # 한국
    ("kospi", "코스피", "", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("^KS11")),
        ("한국은행 ECOS", lambda: ecos("802Y001", "D", "0001000", Y1))]),
    ("kosdaq", "코스닥", "", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("^KQ11")),
        ("한국은행 ECOS", lambda: ecos("802Y001", "D", "0089000", Y1))]),
    ("usdkrw", "원·달러 환율", "원", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("KRW=X")),
        ("한국은행 ECOS", lambda: ecos("731Y001", "D", "0000001", Y1))]),
    ("kr_base", "한국 기준금리", "%", "D", "pp", 2, [
        ("한국은행 ECOS", lambda: ecos("722Y001", "D", "0101000", Y3))]),
    ("ktb3", "국고채 3년", "%", "D", "pp", 3, [
        ("한국은행 ECOS", lambda: ecos("817Y002", "D", "010200000", Y1))]),
    ("ktb10", "국고채 10년", "%", "D", "pp", 3, [
        ("한국은행 ECOS", lambda: ecos("817Y002", "D", "010210000", Y1))]),
    # 미국
    ("spx", "S&P 500", "", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("^GSPC")),
        ("FRED", lambda: fred("SP500", Y1))]),
    ("ndx", "나스닥 종합", "", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("^IXIC")),
        ("FRED", lambda: fred("NASDAQCOM", Y1))]),
    ("dji", "다우존스", "", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("^DJI")),
        ("FRED", lambda: fred("DJIA", Y1))]),
    ("vix", "VIX 공포지수", "", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("^VIX")),
        ("FRED", lambda: fred("VIXCLS", Y1))]),
    ("dxy", "달러인덱스", "", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("DX-Y.NYB"))]),
    ("fed", "미국 기준금리", "%", "D", "pp", 2, [
        ("FRED", lambda: fred("DFEDTARU", Y3))]),
    ("ust2", "미국채 2년", "%", "D", "pp", 2, [
        ("FRED", lambda: fred("DGS2", Y1))]),
    ("ust10", "미국채 10년", "%", "D", "pp", 2, [
        ("FRED", lambda: fred("DGS10", Y1))]),
    ("ust30", "미국채 30년", "%", "D", "pp", 2, [
        ("FRED", lambda: fred("DGS30", Y1))]),
    ("spread", "장단기 금리차 (10년−2년)", "%p", "D", "pp", 2, [
        ("FRED", lambda: fred("T10Y2Y", Y1))]),
    ("cpi", "미국 CPI (전년 대비)", "%", "M", "pp", 1, [
        ("FRED", lambda: fred("CPIAUCSL", Y3, units="pc1", monthly=True))]),
    ("unrate", "미국 실업률", "%", "M", "pp", 1, [
        ("FRED", lambda: fred("UNRATE", Y3, monthly=True))]),
    # 부동산
    ("re_sale", "전국 아파트 매매가격지수", "", "W", "pct", 2, [
        ("한국부동산원 R-ONE", lambda: weekly_national(REB_WEEKLY_SALE))]),
    ("re_jeonse", "전국 아파트 전세가격지수", "", "W", "pct", 2, [
        ("한국부동산원 R-ONE", lambda: weekly_national(REB_WEEKLY_JEONSE))]),
    ("re_trade_seoul", "서울 아파트 매매거래량", "건", "M", "pct", 0, [
        ("한국부동산원 R-ONE", seoul_trades)]),
    ("mortgage", "주담대 금리 (신규취급)", "%", "M", "pp", 2, [
        ("한국은행 ECOS", lambda: ecos("121Y006", "M", "BECBLA0302", Y3))]),
]


def first_ok(sources):
    errors = []
    for name, fn in sources:
        try:
            result = fn()
            if result:
                return result, name
            errors.append(f"{name}: 빈 응답")
        except Exception as e:  # noqa: BLE001 - 출처 하나 실패해도 다음 출처로 넘어간다
            errors.append(f"{name}: {e}")
    raise RuntimeError(" / ".join(errors))


def main():
    try:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("indicators", {})
    except (FileNotFoundError, ValueError):
        old = {}

    ind, errors = {}, []

    def keep_old(key, err):
        err = str(err)
        for secret in (ECOS_KEY, FRED_KEY, REB_KEY):
            if secret:
                err = err.replace(secret, "***")  # 공개 data.json 에 키가 새지 않도록
        errors.append(f"{key}: {err}")
        print(f"  ✗ {key}: {err}", file=sys.stderr)
        if key in old:
            ind[key] = {**old[key], "stale": True}

    for key, name, unit, freq, change, decimals, sources in SERIES:
        try:
            series, source = first_ok(sources)
            ind[key] = {"name": name, "unit": unit, "freq": freq, "change": change,
                        "decimals": decimals, "source": source, "asof": series[-1][0],
                        "series": series}
            print(f"  ✓ {key} ({source}, {series[-1][0]})")
        except Exception as e:  # noqa: BLE001
            keep_old(key, e)

    # 미국 기준금리는 목표 범위 하단도 함께 표시
    if "fed" in ind and not ind["fed"].get("stale"):
        try:
            ind["fed"]["lower"] = fred("DFEDTARL", days_ago(30))[-1][1]
        except Exception as e:  # noqa: BLE001
            errors.append(f"fed lower: {e}")

    try:
        try:
            rows, source = naver_flows(), "네이버 증권"
        except Exception:  # noqa: BLE001 - 네이버 실패 시 ECOS 외국인 순매수만
            rows, source = ecos_foreign_flows(), "한국은행 ECOS"
        ind["kr_flow"] = {"type": "flow", "name": "코스피 투자자별 순매수", "unit": "억원",
                          "freq": "D", "source": source, "asof": rows[-1]["date"], "rows": rows}
        print(f"  ✓ kr_flow ({source}, {rows[-1]['date']})")
    except Exception as e:  # noqa: BLE001
        keep_old("kr_flow", e)

    for key, statbl, label in (("re_rank_sale", REB_WEEKLY_SALE, "매매"),
                               ("re_rank_jeonse", REB_WEEKLY_JEONSE, "전세")):
        try:
            ind[key] = {"type": "rank", "name": f"{label}가격 주간 변동률", "freq": "W",
                        "source": "한국부동산원 R-ONE", **weekly_regions(statbl)}
            print(f"  ✓ {key} ({ind[key]['asof']})")
        except Exception as e:  # noqa: BLE001
            keep_old(key, e)

    fresh = [k for k, v in ind.items() if not v.get("stale")]
    if not fresh:
        print("모든 지표 수집 실패 — data.json 을 바꾸지 않습니다.", file=sys.stderr)
        sys.exit(1)

    data = {"updated": dt.datetime.now(KST).isoformat(timespec="minutes"),
            "indicators": ind, "errors": errors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"완료: {len(fresh)}개 갱신, {len(errors)}개 오류")


if __name__ == "__main__":
    main()
