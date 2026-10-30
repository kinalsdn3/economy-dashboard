#!/usr/bin/env python3
"""경제지표 대시보드 데이터 수집기.

GitHub Actions 에서 하루 한 번 실행되어 data/data.json 을 갱신한다.
표준 라이브러리만 사용한다.

환경변수(GitHub Secrets):
  ECOS_API_KEY  한국은행 ECOS
  FRED_API_KEY  미국 세인트루이스 연준 FRED
  REB_API_KEY   한국부동산원 R-ONE
  DATA_GO_KR_KEY 공공데이터포털 (청약홈 분양정보, 없으면 입주 단지 목록은 수기 입력분만)
"""
import csv
import datetime as dt
import email.utils
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

try:  # 로컬 Windows 처럼 시스템 인증서가 부족한 환경 대비 (Actions 에서는 없어도 됨)
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = None

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "data.json"
SUPPLY_FILE = ROOT / "data" / "manual" / "seoul_supply.json"
KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()

ECOS_KEY = os.environ.get("ECOS_API_KEY", "").strip()
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
REB_KEY = os.environ.get("REB_API_KEY", "").strip()
DATA_KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
SECRETS = [k for k in (ECOS_KEY, FRED_KEY, REB_KEY, DATA_KEY, urllib.parse.quote(DATA_KEY, safe="")) if k]

# R-ONE 통계표 ID
REB_WEEKLY_SALE = "T244183132827305"    # (주) 매매가격지수 - 주간아파트동향
REB_WEEKLY_JEONSE = "T247713133046872"  # (주) 전세가격지수 - 주간아파트동향
REB_MONTHLY_TRADE = "A_2024_00554"      # (월) 행정구역별 아파트매매거래현황
REB_NATIONWIDE = 50001                  # 주간 통계의 '전국' 분류 ID
REB_SEOUL_TRADE = 500002                # 거래현황 통계의 '서울' 분류 ID
METRO = ("서울", "경기", "인천")
SIDO = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
        "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주")

# 크게 움직인 날 카드에 붙일 뉴스 헤드라인: id → (검색어, 기준 변동률 %)
NEWS = {
    "kospi": ("코스피", 1.5), "kosdaq": ("코스닥", 1.5), "usdkrw": ("원달러 환율", 1.0),
    "samsung": ("삼성전자 주가", 3.0), "hynix": ("SK하이닉스 주가", 3.0),
    "spx": ("뉴욕증시 S&P500", 1.5), "ndx": ("나스닥 지수", 1.5), "dji": ("다우지수", 1.5),
    "vix": ("VIX 공포지수", 15.0), "gold": ("국제 금값", 2.0),
}
NAVER_LAND = "https://m.land.naver.com"
SEOUL_CORTAR = "1100000000"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def get_bytes(url, params=None, headers=None, tries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (econ-dashboard)", **(headers or {})})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def get_json(url, params=None, headers=None):
    return json.loads(get_bytes(url, params, headers).decode("utf-8"))


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


def jgb(term):
    """일본 국채 금리 (일본 재무성 CSV). term 예: '3Y', '10Y'."""
    base = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    points = {}
    for path in ("historical/jgbcme_all.csv", "jgbcme.csv"):  # 과거 전체 + 이번 달
        rows = list(csv.reader(io.StringIO(get_bytes(base + path).decode("shift_jis", "replace"))))
        header = next(r for r in rows if r and r[0] == "Date")
        col = header.index(term)
        for r in rows:
            if len(r) > col and r[0][:2] in ("19", "20") and "/" in r[0]:
                try:
                    day = dt.datetime.strptime(r[0], "%Y/%m/%d").date()
                    value = float(r[col])
                except ValueError:
                    continue
                if day >= Y1:
                    points[day.isoformat()] = value
    return [[k, v] for k, v in sorted(points.items())]


def bund(years):
    """독일 국채 금리 (분데스방크 수익률곡선, 잔존만기 years년)."""
    key = f"D.I.ZST.ZI.EUR.S1311.B.A604.R{years:02d}XX.R.A.A._Z._Z.A"
    text = get_bytes(f"https://api.statistiken.bundesbank.de/rest/data/BBSIS/{key}",
                     {"startPeriod": Y1.isoformat()}, headers={"Accept": "text/csv"}).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    header = rows[0]
    t, v = header.index("TIME_PERIOD"), header.index("OBS_VALUE")
    return [[r[t], float(r[v])] for r in rows[1:] if len(r) > v and r[v] not in ("", ".")]


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


def region_key(fullnm):
    # 지도(data/geo/metro.json) 키와 맞춘다: "경기>경부1권>수원시>장안구" → "경기|수원시장안구"
    parts = fullnm.split(">")
    leaf = parts[-1]
    if len(parts) >= 3 and parts[-2].endswith("시") and leaf.endswith("구"):
        leaf = parts[-2] + leaf
    return f"{parts[0]}|{leaf}"


def top_lists(items, n=10):
    up = sorted((i for i in items if round(i["change"], 2) > 0), key=lambda i: -i["change"])[:n]
    down = sorted((i for i in items if round(i["change"], 2) < 0), key=lambda i: i["change"])[:n]
    return {"up": up, "down": down, "count": len(items)}


def weekly_regions(statbl):
    """전주 대비 변동률: 전국 시도 + 수도권 시군구 지도 값, 전국 / 서울 / 경기·인천 TOP10."""
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

    def sido_change(top):
        c = change(top)
        if c is None and top in ("광주", "전남"):  # 2026년 통합 이후 이름이 바뀌었을 경우 대비
            merged = next((n for n in names if ">" not in n and "광주" in n and "전남" in n), None)
            c = merged and change(merged)
        return None if c is None else round(c, 3)

    items, totals, sido_map = {top: [] for top in SIDO}, {}, {}
    nation = change("전국")
    totals["전국"] = None if nation is None else round(nation, 3)
    for top in SIDO:
        totals[top] = sido_map[f"전국|{top}"] = sido_change(top)
        for n in names:
            if n.startswith(top + ">") and not any(m.startswith(n + ">") for m in names):
                c = change(n)
                if c is not None:
                    label = region_label(n)
                    items[top].append({"key": region_key(n), "change": round(c, 3),
                                       "name": label if top == "서울" or (top == "경기" and top not in label)
                                       else f"{top} {label}"})
    # 전국 순위에서는 모든 지역 앞에 시도 이름을 붙인다 (서울 중구 / 인천 중구 구분)
    everything = [{**i, "name": i["name"] if i["name"].startswith(top + " ") else f"{top} {i['name']}"}
                  for top in SIDO for i in items[top]]
    # 하위 지역이 없는 시도(세종)는 시도 자체를 전국 순위에 넣는다
    everything += [{"key": f"전국|{t}", "change": sido_map[f"전국|{t}"], "name": t}
                   for t in SIDO if not items[t] and sido_map[f"전국|{t}"] is not None]
    return {
        "asof": desc[cur], "prev": desc[prev], "totals": totals,
        "map": {**sido_map, **{i["key"]: i["change"] for top in METRO for i in items[top]}},
        "lists": {
            "nation": top_lists(everything),
            "seoul": top_lists(items["서울"]),
            "metro": top_lists(items["경기"] + items["인천"]),
        },
    }


def seoul_trades():
    rows = reb(REB_MONTHLY_TRADE, "MM", CLS_ID=REB_SEOUL_TRADE, ITM_ID=100001,
               START_WRTTIME=days_ago(800).strftime("%Y%m"), END_WRTTIME=TODAY.strftime("%Y%m"))
    by_month = {r["WRTTIME_IDTFR_ID"]: r["DTA_VAL"] for r in rows}
    return [[f"{k[:4]}-{k[4:]}", by_month[k]] for k in sorted(by_month)]


def seoul_listings(old):
    """네이버 부동산 서울 아파트 매물 수(매매·전세·월세)를 구별로 합산해 날짜별로 쌓는다.

    네이버는 현재 매물 수만 보여 주므로 추이는 매일 실행하며 직접 쌓는다 (최근 400일 보관).
    비공식 경로라 막히거나 구조가 바뀌면 실패할 수 있다.
    """
    headers = {"User-Agent": BROWSER_UA, "Referer": f"{NAVER_LAND}/"}
    streak = 0  # 연속 실패 수: 차단(/error/abuse)되면 오래 매달리지 않고 바로 포기한다

    def call(path, cortar):
        nonlocal streak
        time.sleep(1.0)  # 빠르게 연속 호출하면 차단되므로 천천히
        try:
            d = get_json(f"{NAVER_LAND}{path}", {"cortarNo": cortar}, headers)
            streak = 0
            return d["result"]
        except Exception:
            streak += 1
            if streak >= 5:
                raise RuntimeError("네이버 부동산 접속이 막혔어요 (연속 5회 실패)") from None
            return None

    today, counts, failed, total_dongs = TODAY.isoformat(), {}, 0, 0
    for gu in (call("/map/getRegionList", SEOUL_CORTAR) or {}).get("list", []):
        dongs = call("/map/getRegionList", gu["CortarNo"])
        if not dongs:
            continue  # 이 구는 빠지고, 아래에서 25개 구가 안 되면 실패 처리
        deal = lease = rent = 0
        for dong in dongs.get("list", []):
            total_dongs += 1
            rows = call("/complex/ajax/complexListByCortarNo", dong["CortarNo"])
            if rows is None:
                failed += 1
                continue
            for r in rows:
                if r.get("hscpTypeCd") == "A01":  # 아파트만 (오피스텔·분양권 제외)
                    deal += r.get("dealCnt") or 0
                    lease += r.get("leaseCnt") or 0
                    rent += r.get("rentCnt") or 0
        counts[gu["CortarNm"]] = [deal, lease, rent]
    if len(counts) < 25 or failed > total_dongs * 0.05:
        raise RuntimeError(f"네이버 매물 수집 불완전 (구 {len(counts)}개, 동 실패 {failed}/{total_dongs})")
    counts["서울"] = [sum(v[i] for v in counts.values()) for i in range(3)]

    history = (old or {}).get("history") or {}
    cutoff = days_ago(400).isoformat()
    for name, v in counts.items():
        rows = [r for r in history.get(name, []) if r[0] != today and r[0] >= cutoff]
        history[name] = rows + [[today, *v]]
    return {"type": "listing", "name": "서울 아파트 매물 수", "unit": "건", "freq": "D",
            "source": "네이버 부동산", "asof": today, "history": history}


def news_headline(query, up):
    """구글 뉴스 RSS 에서 최근 이틀 안의 대표 헤드라인 하나 (제목·언론사·링크만 사용)."""
    for q in (f"{query} {'상승' if up else '하락'}", query):
        xml = get_bytes("https://news.google.com/rss/search",
                        {"q": f"{q} when:2d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        for item in ET.fromstring(xml).iter("item"):
            title, source = item.findtext("title") or "", item.findtext("source") or ""
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            pub = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
            return {"title": title.strip(), "source": source, "link": item.findtext("link"),
                    "date": pub.astimezone(KST).isoformat(timespec="minutes")}
    return None


def applyhome_seoul():
    """청약홈 분양정보에서 서울 아파트의 입주예정월·공급 세대수 (2021년 이후 모집공고)."""
    if not DATA_KEY:
        return []
    out, page = {}, 1
    while page <= 20:
        d = get_json("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail", {
            "serviceKey": DATA_KEY, "page": page, "perPage": 500,
            "cond[SUBSCRPT_AREA_CODE_NM::EQ]": "서울",
            "cond[RCRIT_PBLANC_DE::GTE]": "2021-01-01",
        })
        rows = d.get("data") or []
        for r in rows:
            ym = str(r.get("MVN_PREARNGE_YM") or "")
            addr = (r.get("HSSPLY_ADRES") or "").split()
            if len(ym) < 6 or len(addr) < 2:
                continue
            out[r.get("HOUSE_MANAGE_NO") or r["HOUSE_NM"]] = {
                "gu": addr[1], "name": (r.get("HOUSE_NM") or "").strip(),
                "units": int(r.get("TOT_SUPLY_HSHLDCO") or 0), "ym": f"{ym[:4]}-{ym[4:6]}", "auto": True}
        if not rows or page * 500 >= (d.get("matchCount") or d.get("totalCount") or 0):
            break
        page += 1
    return list(out.values())


def seoul_supply(errors):
    d = json.loads(SUPPLY_FILE.read_text(encoding="utf-8"))
    try:
        auto = applyhome_seoul()
    except Exception as e:  # noqa: BLE001 - 단지 목록이 실패해도 막대그래프는 보여 준다
        errors.append(f"re_supply_seoul 청약홈: {e}")
        auto = []
    manual = [{**c, "auto": False} for c in d.get("complexes", [])]
    names = {c["name"] for c in manual}
    first_year = TODAY.year
    complexes = sorted((c for c in [*manual, *(a for a in auto if a["name"] not in names)]
                        if int(c["ym"][:4]) >= first_year), key=lambda c: (c["ym"], c["gu"], c["name"]))
    return {"type": "supply", "name": d["name"], "unit": d["unit"], "freq": "Y",
            "source": d["basis"], "asof": d["updated"], "actualUntil": d["actual_until"],
            "years": d["years"], "complexes": complexes, "complexesAuto": bool(DATA_KEY)}


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
    ("samsung", "삼성전자", "원", "D", "pct", 0, [
        ("Yahoo Finance", lambda: yahoo("005930.KS"))]),
    ("hynix", "SK하이닉스", "원", "D", "pct", 0, [
        ("Yahoo Finance", lambda: yahoo("000660.KS"))]),
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
    ("gold", "금 (COMEX 선물)", "$/oz", "D", "pct", 2, [
        ("Yahoo Finance", lambda: yahoo("GC=F"))]),
    ("jgb3", "일본 국채 3년", "%", "D", "pp", 3, [
        ("일본 재무성", lambda: jgb("3Y"))]),
    ("jgb10", "일본 국채 10년", "%", "D", "pp", 3, [
        ("일본 재무성", lambda: jgb("10Y"))]),
    ("bund3", "독일 국채 3년", "%", "D", "pp", 2, [
        ("독일 분데스방크", lambda: bund(3))]),
    ("bund10", "독일 국채 10년", "%", "D", "pp", 2, [
        ("독일 분데스방크", lambda: bund(10))]),
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
        for secret in SECRETS:
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

    # 크게 움직인 지표에 뉴스 헤드라인 한 줄
    for key, (query, threshold) in NEWS.items():
        s = ind.get(key, {}).get("series")
        if not s or len(s) < 2 or ind[key].get("stale"):
            continue
        pct = (s[-1][1] / s[-2][1] - 1) * 100
        if abs(pct) < threshold:
            continue
        try:
            item = news_headline(query, pct > 0)
            if item:
                ind[key]["news"] = item
                print(f"  📰 {key} {pct:+.1f}%: {item['title']}")
        except Exception as e:  # noqa: BLE001 - 뉴스는 없어도 된다
            errors.append(f"{key} 뉴스: {e}")

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

    try:
        ind["re_listing_seoul"] = seoul_listings(old.get("re_listing_seoul"))
        print(f"  ✓ re_listing_seoul (서울 매물 {ind['re_listing_seoul']['history']['서울'][-1][1:]})")
    except Exception as e:  # noqa: BLE001
        keep_old("re_listing_seoul", e)

    try:
        ind["re_supply_seoul"] = seoul_supply(errors)
        print(f"  ✓ re_supply_seoul (수기 입력 + 단지 {len(ind['re_supply_seoul']['complexes'])}개)")
    except Exception as e:  # noqa: BLE001
        keep_old("re_supply_seoul", e)

    fresh =[k for k, v in ind.items() if not v.get("stale")]
    if not fresh:
        print("모든 지표 수집 실패 — data.json 을 바꾸지 않습니다.", file=sys.stderr)
        sys.exit(1)

    for secret in SECRETS:  # keep_old 를 거치지 않은 오류 문구도 한 번 더 가린다
        errors = [e.replace(secret, "***") for e in errors]
    data = {"updated": dt.datetime.now(KST).isoformat(timespec="minutes"),
            "indicators": ind, "errors": errors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"완료: {len(fresh)}개 갱신, {len(errors)}개 오류")


if __name__ == "__main__":
    main()
