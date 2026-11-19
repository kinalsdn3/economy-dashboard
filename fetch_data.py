#!/usr/bin/env python3
"""경제지표 대시보드 데이터 수집기.

GitHub Actions 에서 하루 두 번(07:10 미국 마감 반영, 18:30 한국 마감 반영) 실행되어 data/data.json 을 갱신한다.
표준 라이브러리만 사용한다.

환경변수(GitHub Secrets):
  ECOS_API_KEY  한국은행 ECOS
  FRED_API_KEY  미국 세인트루이스 연준 FRED
  REB_API_KEY   한국부동산원 R-ONE
  DATA_GO_KR_KEY 공공데이터포털 (청약홈 분양정보, 없으면 입주 단지 목록은 수기 입력분만)
"""
import calendar
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
import urllib.error
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
KB_FILE = ROOT / "data" / "kb_weekly.json"  # 크기가 커서(2008년~) 따로 두고 화면에서 필요할 때만 불러온다
# 신고가 판단용 단지별 최고가표 — 사이트에 올리지 않고 GitHub Actions 캐시에 보관한다
RT_STATE = ROOT / "state" / "rt_state.json"
RT_START = "2019-01"         # 이 달부터의 거래로 '직전 최고가'를 판단 (2021년 고점 포함)
RT_BUDGET = 1500             # 실행 한 번에 과거 자료 모으기에 쓸 최대 요청 수 (하루 한도 10,000회)
RT_CLASSES = {59: "25평형", 84: "34평형"}  # 전용 59㎡대 / 84㎡대만 본다 (흔히 부르는 평형)
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
# 미국 S&P 500 11개 섹터 ETF (SPDR) — 업종별 등락
SECTORS = [
    ("XLK", "정보기술"), ("XLC", "커뮤니케이션"), ("XLY", "임의소비재"), ("XLP", "필수소비재"),
    ("XLE", "에너지"), ("XLF", "금융"), ("XLV", "헬스케어"), ("XLI", "산업재"),
    ("XLB", "소재"), ("XLRE", "부동산"), ("XLU", "유틸리티"),
]
# 목요일 14시 실행: 부동산원 주간 아파트 동향(목요일 14시 공표)만 새로 받고 나머지는 그대로 둔다
WEEKLY_ONLY = os.environ.get("WEEKLY_ONLY") == "1"
WEEKLY_KEYS = {"re_sale", "re_jeonse", "re_rank_sale", "re_rank_jeonse"}


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
            "metro": top_lists(items["경기"]),  # 지도는 경기만 (인천 제외)
        },
    }


def seoul_trades():
    rows = reb(REB_MONTHLY_TRADE, "MM", CLS_ID=REB_SEOUL_TRADE, ITM_ID=100001,
               START_WRTTIME=days_ago(800).strftime("%Y%m"), END_WRTTIME=TODAY.strftime("%Y%m"))
    by_month = {r["WRTTIME_IDTFR_ID"]: r["DTA_VAL"] for r in rows}
    return [[f"{k[:4]}-{k[4:]}", by_month[k]] for k in sorted(by_month)]



def kb_weekly():
    """KB부동산 주간 아파트 매매·전세 증감률(%) — 서울·경기 시군구, 2008년 4월부터 전체.

    결과는 data/kb_weekly.json 에 따로 쓰고, data.json 에는 요약(기준일)만 남긴다.
    """
    url = "https://data-api.kbland.kr/bfmstat/weekMnthlyHuseTrnd/prcIndxInxrdcRt"
    regions, dates = {"seoul": {}, "gyeonggi": {}}, set()
    raw = {}
    for view, code in (("seoul", "11"), ("gyeonggi", "41")):
        for kind, trade in (("sale", "01"), ("jeonse", "02")):
            body = get_json(url, {"월간주간구분코드": "02", "매물종별구분": "01",
                                  "매매전세코드": trade, "지역코드": code})["dataBody"]
            if str(body.get("resultCode")) != "11000":
                raise RuntimeError(f"KB {view}/{kind}: {body.get('message') or body.get('resultCode')}")
            days = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in body["data"]["날짜리스트"]]
            dates.update(days)
            for r in body["data"]["데이터리스트"]:
                reg = regions[view].setdefault(r["지역코드"], {"code": r["지역코드"], "name": r["지역명"]})
                raw[(view, r["지역코드"], kind)] = dict(zip(days, r["dataList"]))
                reg.setdefault("kinds", set()).add(kind)
    dates = sorted(dates)
    if len(dates) < 100:
        raise RuntimeError(f"KB 주간 데이터 부족 ({len(dates)}주)")
    out = {}
    for view, regs in regions.items():
        out[view] = []
        for reg in regs.values():
            if reg.pop("kinds") != {"sale", "jeonse"}:
                continue
            for kind in ("sale", "jeonse"):
                vals = raw[(view, reg["code"], kind)]  # 파일 크기를 줄이려고 1/1000 % 단위 정수로 저장
                reg[kind] = [None if vals.get(d) is None else round(vals[d] * 1000) for d in dates]
            out[view].append(reg)
        if len(out[view]) < 20:
            raise RuntimeError(f"KB {view} 지역 수 부족 ({len(out[view])}개)")
    KB_FILE.write_text(json.dumps({"dates": dates, "scale": 1000, "regions": out},
                                  ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    return {"type": "kb", "name": "KB 주간 아파트 증감률", "freq": "W", "source": "KB부동산 데이터허브",
            "asof": dates[-1], "first": dates[0], "file": "data/kb_weekly.json"}


def kb_market():
    """KB 주간 시장 분위기 지수 (중개업소 설문): 매수우위 / 매매거래활발 / 전세수급, 최근 3년.

    지수 = 100 + (앞 응답 비중 − 뒤 응답 비중). 100 보다 크면 매수자 많음 / 거래 활발 / 공급 부족.
    """
    url = "https://data-api.kbland.kr/bfmstat/weekMnthlyHuseTrnd/maktTrnd"
    menus = (("buy", "01", "매수우위지수", ("매수자많음", "비슷함", "매도자많음"), ("매수자 많음", "비슷함", "매도자 많음")),
             ("trade", "02", "매매거래지수", ("활발함", "보통", "한산함"), ("활발함", "보통", "한산함")),
             ("jeonse", "03", "전세수급지수", ("공급부족", "공급적절", "공급충분"), ("공급 부족", "적절", "공급 충분")))
    keep = ("전국", "서울", "강북14개구", "강남11개구", "수도권", "경기", "인천", "6개광역시", "기타지방")
    start = days_ago(3 * 365 + 7).strftime("%Y%m%d")
    indexes, asof = {}, None
    for key, menu, label, fields, names in menus:
        body = get_json(url, {"메뉴코드": menu, "월간주간구분코드": "02"})["dataBody"]
        if str(body.get("resultCode")) != "11000":
            raise RuntimeError(f"KB {label}: {body.get('message') or body.get('resultCode')}")
        days = body["data"]["날짜리스트"]
        cut = next(i for i, d in enumerate(days + ["99999999"]) if d >= start)
        regions = {}
        for r in body["data"]["데이터리스트"]:
            vals = [(v or {}).get(label) for v in r["dataList"][cut:]]
            if r["지역명"] not in keep or vals[-1:] in ([], [None]):
                continue
            last = r["dataList"][-1]
            regions[r["지역명"]] = {
                "values": [None if v is None else round(v, 1) for v in vals],
                "parts": [[n, round(last.get(f) or 0, 1)] for f, n in zip(fields, names)],
            }
        if "서울" not in regions:
            raise RuntimeError(f"KB {label}: 서울 데이터 없음")
        dates = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days[cut:]]  # 지역마다 같은 날짜라 한 번만 저장
        indexes[key] = {"label": label, "dates": dates, "regions": {n: regions[n] for n in keep if n in regions}}
        asof = max(asof or "", dates[-1])
    return {"type": "kbmarket", "name": "KB 시장 분위기 지수", "freq": "W", "source": "KB부동산 데이터허브",
            "asof": asof, "indexes": indexes}


# ---------- 서울·경기 아파트 신고가 (국토교통부 실거래가) ----------

def month_add(ym, n):
    y, m = map(int, ym.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y}-{m + 1:02d}"


class ApiCodeError(RuntimeError):
    """API 가 오류 코드로 답함 (지역 코드 문제 등). 키·호출 한도 문제는 메시지에 KEY/LIMIT 이 들어 있다."""


def data_go(path, params):
    """공공데이터포털 API 한 번 호출 → (항목 dict 목록, 전체 건수). XML·JSON 응답 모두 처리."""
    raw = get_bytes(f"https://apis.data.go.kr/1613000/{path}", {"serviceKey": DATA_KEY, **params})
    text = raw.decode("utf-8", "replace").strip()
    if text.startswith("{"):
        d = json.loads(text)
        body = d.get("response", d).get("body", {})
        items = (body.get("items") or {})
        items = items.get("item", []) if isinstance(items, dict) else items
        items = [items] if isinstance(items, dict) else items
        return items, int(body.get("totalCount") or len(items))
    root = ET.fromstring(raw)
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or ""
    if code not in ("000", "00", ""):
        raise ApiCodeError(f"{path}: {code} {root.findtext('.//resultMsg') or root.findtext('.//returnAuthMsg') or ''}")
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or len(items))


def rt_deals(code, ym):
    """한 시군구·한 달의 아파트 매매 신고 (전용 59/84㎡대만)."""
    out, page = [], 1
    while True:
        items, total = data_go("RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
                               {"LAWD_CD": code, "DEAL_YMD": ym.replace("-", ""), "pageNo": page, "numOfRows": 1000})
        for it in items:
            try:
                area = float(it.get("excluUseAr") or 0)
                if int(area) not in RT_CLASSES:
                    continue
                date = f"{int(it['dealYear'])}-{int(it['dealMonth']):02d}-{int(it['dealDay']):02d}"
                price = int(str(it["dealAmount"]).replace(",", "").strip())
            except (KeyError, ValueError):
                continue
            seq = it.get("aptSeq") or f"{code}-{it.get('aptNm', '')}-{it.get('jibun', '')}"
            out.append({
                "seq": seq, "cls": int(area), "area": round(area, 2), "date": date, "price": price,
                "floor": (it.get("floor") or "").strip(), "name": (it.get("aptNm") or "").strip(),
                "dong": (it.get("umdNm") or "").strip(), "year": (it.get("buildYear") or "").strip(),
                "cancel": bool((it.get("cdealType") or "").strip()), "direct": "직거래" in (it.get("dealingGbn") or ""),
                "id": "|".join([seq, date, (it.get("floor") or "").strip(), str(price), f"{area:.2f}",
                                (it.get("aptDong") or "").strip()]),
            })
        if not items or page * 1000 >= total:
            return sorted(out, key=lambda d: d["date"])
        page += 1


def metro_sgg():
    """서울·경기 시군구 코드(5자리)와 이름 — KB 지역 목록에서 가져온다 (구가 있는 시는 시 코드도 포함)."""
    out = []
    for code in ("11", "41"):
        body = get_json("https://data-api.kbland.kr/bfmstat/weekMnthlyHuseTrnd/prcIndxInxrdcRt",
                        {"월간주간구분코드": "02", "매물종별구분": "01", "매매전세코드": "01", "지역코드": code})["dataBody"]
        for r in body["data"]["데이터리스트"]:
            c = r["지역코드"]
            if len(c) == 10 and c[2:5] != "000":
                out.append([c[:5], r["지역명"]])
    if len(out) < 60:
        raise RuntimeError(f"서울·경기 시군구 목록 부족 ({len(out)}개)")
    return out


def norm_name(s):
    return re.sub(r"[\s()\[\]·.,\-]|아파트|APT", "", s or "", flags=re.I)


def kapt_units(state, code, deal):
    """K-apt 공동주택 정보에서 세대수 (이름으로 단지를 맞춘다). 못 찾으면 None."""
    lists = state.setdefault("kapt", {})
    if code not in lists:
        rows = None
        for path in ("AptListService3/getSigunguAptList3", "AptListService2/getSigunguAptList"):
            try:
                items, _ = data_go(path, {"sigunguCode": code, "pageNo": 1, "numOfRows": 3000})
                rows = [[i.get("kaptCode"), i.get("kaptName"), i.get("as3") or i.get("as4") or ""] for i in items]
                break
            except Exception:  # noqa: BLE001 - 다음 버전 주소로
                continue
        if rows is None:
            raise RuntimeError("공동주택 단지 목록 API 응답 없음 (활용신청 필요)")
        lists[code] = rows
    target = norm_name(deal["name"])
    cands = [r for r in lists[code] if r[0] and (norm_name(r[1]) == target
             or (len(target) >= 3 and (target in norm_name(r[1]) or norm_name(r[1]) in target)))]
    cands.sort(key=lambda r: (r[2] != deal["dong"], norm_name(r[1]) != target))
    if not cands:
        return None
    for path in ("AptBasisInfoServiceV4/getAphusBassInfoV4", "AptBasisInfoServiceV5/getAphusBassInfoV5",
                 "AptBasisInfoServiceV3/getAphusBassInfoV3"):
        try:
            items, _ = data_go(path, {"kaptCode": cands[0][0]})
        except Exception:  # noqa: BLE001
            continue
        for it in items:
            n = str(it.get("kaptdaCnt") or it.get("hoCnt") or "").replace(",", "")
            if n.isdigit() and int(n) > 0:
                return int(n)
    raise RuntimeError("공동주택 기본정보 API 응답 없음 (활용신청 필요)")


def apply_deal(state, deal, today, record):
    """거래 하나를 최고가표에 반영. record=True 이고 최고가를 넘으면 신고가 목록에 넣는다."""
    key = f"{deal['seq']}|{deal['cls']}"
    best = state["max"].get(key)  # [가격, 날짜, 거래 ID, 직전 가격, 직전 날짜]
    if deal["cancel"]:  # 해제(취소)된 거래: 최고가였다면 직전 값으로 되돌리고 신고가 목록에서 뺀다
        if best and best[2] == deal["id"]:
            state["max"][key] = [best[3], best[4], "", None, None] if best[3] else None
            if state["max"][key] is None:
                del state["max"][key]
        state["highs"] = [h for h in state["highs"] if h["id"] != deal["id"]]
        return
    if best is None:
        state["max"][key] = [deal["price"], deal["date"], deal["id"], None, None]
    elif deal["price"] > best[0]:
        state["max"][key] = [deal["price"], deal["date"], deal["id"], best[0], best[1]]
        if record:
            state["highs"].append({**{k: deal[k] for k in ("id", "seq", "cls", "area", "date", "price", "floor",
                                                           "name", "dong", "year", "direct")},
                                   "prev": best[0], "prevDate": best[1], "found": today})


def apt_highs(errors):
    """서울·경기 아파트(전용 59/84㎡대) 신고가 — 단지·면적별 최고가표를 Actions 캐시에 두고 매일 새 신고분과 비교."""
    if not DATA_KEY:
        raise RuntimeError("DATA_GO_KR_KEY 없음")
    try:
        state = json.loads(RT_STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        state = {}
    if state.get("start") != RT_START:
        state = {"start": RT_START, "next": RT_START, "pos": 0, "max": {}, "seen": {}, "highs": []}
    if not state.get("codes"):
        state["codes"] = metro_sgg()
    codes = state["codes"]
    today, this_month = TODAY.isoformat(), TODAY.strftime("%Y-%m")
    recent = month_add(this_month, -2)  # 이 달 이후 거래는 '이미 본 거래' 목록으로 새 신고분을 가려낸다
    recent_cut = days_ago(30).isoformat()
    began = time.time()

    def take(deals, record):
        for d in deals:
            if d["date"][:7] >= recent:
                if d["id"] in state["seen"] and not d["cancel"]:
                    continue
                state["seen"][d["id"]] = d["date"][:7]
            apply_deal(state, d, today, record(d))

    def save():
        RT_STATE.parent.mkdir(parents=True, exist_ok=True)
        RT_STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    skipped = []

    def deals(code, ym):
        # 지역 코드 하나가 오류로 답하면(옛 시 코드 등) 그 지역·달만 건너뛴다.
        # 키·한도·접속 문제면 바로 멈추고, 다음 실행 때 같은 자리부터 다시 한다 (get_bytes 가 이미 3번 재시도)
        bad = state.setdefault("bad", {})
        if bad.get(code, 0) >= 3:  # 세 번 연속 오류 난 코드는 더 묻지 않는다
            return []
        try:
            deals_ = rt_deals(code, ym)
            bad.pop(code, None)
            return deals_
        except Exception as e:  # noqa: BLE001
            msg = f"{code}/{ym}: {e}"
            if not isinstance(e, ApiCodeError) or re.search(r"KEY|LIMIT", str(e), re.I):
                raise RuntimeError(f"실거래가 조회 실패 — {msg}") from None
            bad[code] = bad.get(code, 0) + 1
            skipped.append(msg)
            return []

    try:
        # 1) 과거 자료 모으기: RT_START 부터 이번 달까지 한 달씩, 시군구별로 (실행마다 RT_BUDGET 만큼 이어서)
        calls = 0
        while state["next"] <= this_month and calls < RT_BUDGET and time.time() - began < 25 * 60:
            code = codes[state["pos"]][0]
            take(deals(code, state["next"]), lambda d: d["date"] >= recent_cut)  # 최근 30일 거래면 첫 목록에
            calls += 1
            state["pos"] += 1
            if state["pos"] >= len(codes):
                state["pos"], state["next"] = 0, month_add(state["next"], 1)

        # 2) 다 모았으면 매일: 지난달·이번 달 신고분 중 처음 보는 거래만 비교 (신고는 계약 후 30일 안)
        if state["next"] > this_month and calls == 0:
            for code, _ in codes:
                for ym in (month_add(this_month, -1), this_month):
                    take(deals(code, ym), lambda d: True)
    finally:  # 중간에 실패해도 여기까지 모은 최고가표는 남긴다
        state["seen"] = {k: v for k, v in state["seen"].items() if v >= recent}
        state["highs"] = [h for h in state["highs"] if h["found"] >= recent_cut]
        save()
    filling = state["next"] <= this_month

    # 3) 화면에 보낼 목록: 최근 7일에 찾은 신고가, 서울·경기 각각 상승폭 큰 순 300건까지 + 세대수
    names = dict(map(tuple, codes))
    week = days_ago(7).isoformat()
    units, lookups, unit_err = state.setdefault("units", {}), 0, None
    out = {"seoul": [], "gyeonggi": []}
    for h in sorted((h for h in state["highs"] if h["found"] >= week), key=lambda h: h["prev"] - h["price"]):
        code = h["seq"][:5]
        region = "seoul" if code.startswith("11") else "gyeonggi"
        if len(out[region]) >= 300:
            continue
        u = units.get(h["seq"])
        if (u is None or (u[0] is None and u[1] < days_ago(30).isoformat())) and lookups < 200 and not unit_err:
            lookups += 1
            try:
                units[h["seq"]] = u = [kapt_units(state, code, h), today]
            except Exception as e:  # noqa: BLE001 - 세대수는 없어도 보여 준다
                unit_err = str(e)
        out[region].append({**{k: h[k] for k in h if k not in ("id", "seq")},
                            "sgg": names.get(code, ""), "units": u[0] if u else None})
    if unit_err:
        errors.append(f"re_highs 세대수: {unit_err}")
    if skipped:
        errors.append(f"re_highs 일부 지역 건너뜀 {len(skipped)}건: {skipped[0]}")
    save()  # 세대수 조회 결과도 저장
    total_months = (int(this_month[:4]) - int(RT_START[:4])) * 12 + int(this_month[5:]) - int(RT_START[5:]) + 1
    done_months = total_months if not filling else \
        (int(state["next"][:4]) - int(RT_START[:4])) * 12 + int(state["next"][5:]) - int(RT_START[5:])
    return {"type": "highs", "name": "서울·경기 아파트 신고가", "freq": "D", "source": "국토교통부 실거래가",
            "asof": today, "progress": None if not filling else [done_months, total_months],
            "since": RT_START, "items": out}


def period_changes(series):
    """마지막 종가 기준 1일 / 1주 / 1개월 / 연초 이후 등락률(%)."""
    last_day, last = series[-1]
    d = dt.date.fromisoformat(last_day)
    m = d.month - 1 or 12
    y = d.year - (d.month == 1)
    month_ago = dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))

    def since(day):  # 그 날짜(포함) 이전 마지막 종가 대비
        base = next((v for k, v in reversed(series) if k <= day.isoformat()), None)
        return None if not base else round((last / base - 1) * 100, 2)
    return {"1D": round((last / series[-2][1] - 1) * 100, 2),
            "1W": since(d - dt.timedelta(days=7)), "1M": since(month_ago),
            "YTD": since(dt.date(d.year - 1, 12, 31))}


def us_sectors():
    rows, errors = [], []
    for sym, name in SECTORS:
        try:
            s = yahoo(sym)
            rows.append({"sym": sym, "name": name, "asof": s[-1][0], "chg": period_changes(s)})
        except Exception as e:  # noqa: BLE001 - 한두 개 빠져도 나머지는 보여 준다
            errors.append(f"{sym}: {e}")
    if len(rows) < 9:
        raise RuntimeError(f"섹터 ETF {len(rows)}/11개만 수집 — {' / '.join(errors)}")
    spy = yahoo("SPY")
    return {"type": "sectors", "name": "미국 업종별 등락", "freq": "D", "source": "Yahoo Finance (SPDR 섹터 ETF)",
            "asof": max(r["asof"] for r in rows), "rows": rows,
            "bench": {"sym": "SPY", "name": "S&P 500", "chg": period_changes(spy)}}


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
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        prev = {}
    old = prev.get("indicators", {})

    if WEEKLY_ONLY:  # 주간 지표 말고는 지난 실행 결과(지연 표시·오류 포함)를 그대로 쓴다
        ind = {k: v for k, v in old.items() if k not in WEEKLY_KEYS}
        errors = [e for e in prev.get("errors", []) if e.split(":")[0] not in WEEKLY_KEYS]
    else:
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
        if WEEKLY_ONLY and key not in WEEKLY_KEYS:
            continue
        try:
            series, source = first_ok(sources)
            ind[key] = {"name": name, "unit": unit, "freq": freq, "change": change,
                        "decimals": decimals, "source": source, "asof": series[-1][0],
                        "series": series}
            print(f"  ✓ {key} ({source}, {series[-1][0]})")
        except Exception as e:  # noqa: BLE001
            keep_old(key, e)

    for key, statbl, label in (("re_rank_sale", REB_WEEKLY_SALE, "매매"),
                               ("re_rank_jeonse", REB_WEEKLY_JEONSE, "전세")):
        try:
            ind[key] = {"type": "rank", "name": f"{label}가격 주간 변동률", "freq": "W",
                        "source": "한국부동산원 R-ONE", **weekly_regions(statbl)}
            print(f"  ✓ {key} ({ind[key]['asof']})")
        except Exception as e:  # noqa: BLE001
            keep_old(key, e)

    if WEEKLY_ONLY:
        if not any(k in ind and not ind[k].get("stale") for k in WEEKLY_KEYS):
            print("주간 지표 수집 실패 — data.json 을 바꾸지 않습니다.", file=sys.stderr)
            sys.exit(1)
        write(prev.get("updated"), ind, errors)  # 상단 업데이트 시각은 마지막 전체 실행 기준 유지
        print("완료: 주간 아파트 동향만 갱신")
        return

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
        ind["us_sectors"] = us_sectors()
        print(f"  ✓ us_sectors ({ind['us_sectors']['asof']}, {len(ind['us_sectors']['rows'])}개)")
    except Exception as e:  # noqa: BLE001
        keep_old("us_sectors", e)

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

    try:
        ind["re_kb_weekly"] = kb_weekly()
        print(f"  ✓ re_kb_weekly ({ind['re_kb_weekly']['asof']}, {KB_FILE.stat().st_size // 1024} KB)")
    except Exception as e:  # noqa: BLE001 - 실패하면 kb_weekly.json 은 지난 파일 그대로
        keep_old("re_kb_weekly", e)

    try:
        ind["re_kb_market"] = kb_market()
        print(f"  ✓ re_kb_market ({ind['re_kb_market']['asof']})")
    except Exception as e:  # noqa: BLE001
        keep_old("re_kb_market", e)

    try:
        ind["re_highs"] = apt_highs(errors)
        h = ind["re_highs"]
        print(f"  ✓ re_highs (서울 {len(h['items']['seoul'])}건, 경기 {len(h['items']['gyeonggi'])}건"
              + (f", 과거 자료 모으는 중 {h['progress'][0]}/{h['progress'][1]}개월)" if h["progress"] else ")"))
    except Exception as e:  # noqa: BLE001 - 실패해도 최고가표는 지난 캐시 그대로
        keep_old("re_highs", e)

    try:
        ind["re_supply_seoul"] = seoul_supply(errors)
        print(f"  ✓ re_supply_seoul (수기 입력 + 단지 {len(ind['re_supply_seoul']['complexes'])}개)")
    except Exception as e:  # noqa: BLE001
        keep_old("re_supply_seoul", e)

    fresh =[k for k, v in ind.items() if not v.get("stale")]
    if not fresh:
        print("모든 지표 수집 실패 — data.json 을 바꾸지 않습니다.", file=sys.stderr)
        sys.exit(1)

    write(None, ind, errors)
    print(f"완료: {len(fresh)}개 갱신, {len(errors)}개 오류")


def write(updated, ind, errors):
    for secret in SECRETS:  # keep_old 를 거치지 않은 오류 문구도 한 번 더 가린다
        errors = [e.replace(secret, "***") for e in errors]
    data = {"updated": updated or dt.datetime.now(KST).isoformat(timespec="minutes"),
            "indicators": ind, "errors": errors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
