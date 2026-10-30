(() => {
  "use strict";

  // 탭별 화면 구성. id 는 data/data.json 의 indicators 키.
  const LAYOUT = {
    kr: [
      { title: "지수 · 환율", ids: ["kospi", "kosdaq", "usdkrw"] },
      { title: "개별 종목", ids: ["samsung", "hynix"] },
      { title: "코스피 수급", ids: ["kr_flow"] },
      { title: "금리", ids: ["kr_base", "ktb3", "ktb10"] },
    ],
    us: [
      { title: "지수", ids: ["spx", "ndx", "dji", "vix", "dxy"] },
      { title: "업종별 등락 · 섹터 ETF", ids: ["us_sectors"] },
      { title: "금리", ids: ["fed", "ust2", "ust10", "ust30", "spread"] },
      { title: "금 · 해외 국채", ids: ["gold", "jgb3", "jgb10", "bund3", "bund10"] },
      { title: "물가 · 고용", ids: ["cpi", "unrate"] },
    ],
    re: [
      { title: "전국 아파트 · 주간 변동률", ids: ["re_sale", "re_jeonse"] },
      { title: "지역별 주간 변동률", ids: ["re_map"] },
      { title: "서울 아파트 매물", ids: ["re_listing_seoul"] },
      { title: "서울 아파트 입주물량", ids: ["re_supply_seoul"] },
      { title: "거래 · 대출", ids: ["re_trade_seoul", "mortgage"] },
    ],
  };
  const NAMES = {
    kospi: "코스피", kosdaq: "코스닥", usdkrw: "원·달러 환율", kr_flow: "코스피 투자자별 순매수",
    samsung: "삼성전자", hynix: "SK하이닉스",
    kr_base: "한국 기준금리", ktb3: "국고채 3년", ktb10: "국고채 10년",
    spx: "S&P 500", ndx: "나스닥 종합", dji: "다우존스", vix: "VIX 공포지수", dxy: "달러인덱스",
    us_sectors: "미국 업종별 등락",
    fed: "미국 기준금리", ust2: "미국채 2년", ust10: "미국채 10년", ust30: "미국채 30년",
    spread: "장단기 금리차 (10년−2년)", cpi: "미국 CPI (전년 대비)", unrate: "미국 실업률",
    gold: "금 (COMEX 선물)", jgb3: "일본 국채 3년", jgb10: "일본 국채 10년",
    bund3: "독일 국채 3년", bund10: "독일 국채 10년",
    re_sale: "전국 아파트 매매가격지수", re_jeonse: "전국 아파트 전세가격지수",
    re_listing_seoul: "서울 아파트 매물 수", re_supply_seoul: "서울 아파트 입주물량",
    re_trade_seoul: "서울 아파트 매매거래량", mortgage: "주담대 금리 (신규취급)",
  };
  const NOTES = {
    vix: "20 이상은 불안, 30 이상은 공포 구간으로 봐요.",
    spread: "0 아래면 장단기 금리 역전이에요.",
    gold: "1트로이온스(31.1g)당 달러 가격이에요.",
    re_trade_seoul: "최근 1~2개월은 신고 기한(30일)이 남아 적게 잡힐 수 있어요.",
  };
  const PERIOD = { D: "전일 대비", W: "전주 대비", M: "전월 대비" };
  const WIDE = new Set(["kr_flow", "re_sale", "re_jeonse", "re_trade_seoul", "re_listing_seoul"]);
  const BAR_IDS = new Set(["re_trade_seoul"]);
  const COUNT_UNITS = new Set(["건", "가구"]);
  const MAP_VIEWS = { nation: "전국", seoul: "서울", metro: "경기 · 인천" };
  const LIST_KINDS = [["deal", "매매"], ["lease", "전세"], ["rent", "월세"]];
  const SECTOR_PERIODS = { "1D": "1일", "1W": "1주", "1M": "1개월", YTD: "연초 이후" };
  const WEEKS_SHOWN = 26;
  // 지도 색: 하락은 진한 파랑, 상승은 빨강 (연한 → 진한)
  const RED = ["#fde0dc", "#f6b2a8", "#ec8274", "#d94f41", "#ad2519"];
  const BLUE = ["#dce8fb", "#aac8f2", "#6fa2e6", "#346fcb", "#0d3a87"];

  const $ = (s, el = document) => el.querySelector(s);
  const main = $("#main");
  const tip = $("#tip");
  let DATA = null;
  let GEO = null;
  let geoLoading = null;
  let tab = "kr";
  let rankKind = "sale";
  let mapView = "nation";
  let listKind = 0;       // 매물: 0 매매, 1 전세, 2 월세
  let listGu = "서울";    // 매물: "서울" 이면 서울 전체
  let listOpen = false;   // 매물: 구별 목록 펼침 여부
  let supplyYear = null;  // 입주물량: 단지 목록을 펼친 연도
  let sectorPeriod = "1D"; // 섹터: 등락 기간

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장 불가 환경 */ } },
  };

  // ---------- 서식 ----------
  const nfCache = {};
  const fmt = (v, d = 2) =>
    (nfCache[d] ||= new Intl.NumberFormat("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d })).format(v);
  const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
  const dirOf = (v, d = 2) => { const r = +v.toFixed(d); return r > 0 ? "up" : r < 0 ? "down" : "flat"; };
  const ARROW = { up: "▲", down: "▼", flat: "–" };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function shortDate(s, freq) {
    if (!s) return "";
    const [y, m, d] = s.split("-");
    if (!d) return `${y}.${m}`;
    return freq === "W" ? `${+m}.${+d} 주` : `${+m}.${+d}`;
  }
  function longDate(s) {
    const [y, m, d] = s.split("-");
    return d ? `${y}.${m}.${d}` : `${y}.${m}`;
  }
  function spanLabel(series, freq) {
    if (freq === "W") return `최근 ${Math.min(WEEKS_SHOWN, series.length - 1)}주`;
    const [y1, m1] = series[0][0].split("-").map(Number);
    const [y2, m2] = series[series.length - 1][0].split("-").map(Number);
    const months = (y2 - y1) * 12 + (m2 - m1);
    return months >= 11 ? `${Math.round(months / 12)}년` : `${months}개월`;
  }
  function eok(v) {
    if (v == null) return "—";
    const a = Math.abs(v);
    return a >= 10000 ? `${sign(v)}${fmt(a / 10000, 1)}조` : `${sign(v)}${fmt(a, 0)}억`;
  }
  function withUnit(text, unit) {
    if (unit && unit.startsWith("$")) return `$${text}<small>${esc(unit.slice(1))}</small>`;
    return unit ? `${text}<small>${esc(unit)}</small>` : text;
  }
  function plainUnit(text, unit) {
    return unit && unit.startsWith("$") ? `$${text}` : `${text}${unit || ""}`;
  }
  // "수원시장안구" → "수원 장안구"
  const gyeonggiName = (n) => n.replace(/^(.+?)시(.+구)$/, "$1 $2");
  const pctText = (v) => `${sign(+v.toFixed(2))}${fmt(Math.abs(v), 2)}%`;

  // ---------- 툴팁 ----------
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    const left = Math.min(Math.max(8, x - r.width / 2), innerWidth - r.width - 8);
    const top = y - r.height - 10 < 8 ? y + 16 : y - r.height - 10;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }
  const hideTip = () => { tip.hidden = true; };

  // ---------- 차트 ----------
  const SVG = "http://www.w3.org/2000/svg";
  const el = (name, attrs) => {
    const n = document.createElementNS(SVG, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };

  function bindHover(box, count, xOf, onIndex, onLeave) {
    const pick = (e) => {
      const r = box.getBoundingClientRect();
      const x = e.clientX - r.left;
      let best = 0, bestD = Infinity;
      for (let i = 0; i < count; i++) {
        const d = Math.abs(xOf(i) - x);
        if (d < bestD) { bestD = d; best = i; }
      }
      onIndex(best, e.clientX, r.top);
    };
    box.addEventListener("pointermove", pick);
    box.addEventListener("pointerdown", pick);
    box.addEventListener("pointerleave", () => { onLeave(); hideTip(); });
    box.addEventListener("pointercancel", () => { onLeave(); hideTip(); });
  }

  // 1개 계열 선 차트 (기간 첫 값 대비 상승이면 빨강, 하락이면 파랑)
  function lineChart(box, series, label) {
    const w = box.clientWidth, h = box.clientHeight;
    if (!w || series.length < 2) return;
    const vals = series.map((p) => p[1]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = 4;
    const x = (i) => (i / (series.length - 1)) * w;
    const y = (v) => pad + (1 - (v - lo) / (hi - lo)) * (h - pad * 2);
    const d = dirOf(vals[vals.length - 1] - vals[0], 6);
    const color = d === "down" ? "var(--down)" : d === "up" ? "var(--up)" : "var(--flat)";
    const soft = d === "down" ? "var(--down-soft)" : d === "up" ? "var(--up-soft)" : "transparent";

    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": `${label} 추이` });
    const line = series.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
    svg.append(
      el("path", { d: `${line}L${w},${h}L0,${h}Z`, style: `fill:${soft};opacity:.7` }),
      el("line", { x1: 0, x2: w, y1: y(vals[0]), y2: y(vals[0]), style: "stroke:var(--axis)", "stroke-dasharray": "2 3" }),
      el("path", { d: line, style: `fill:none;stroke:${color}`, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }),
    );
    const guide = el("line", { y1: 0, y2: h, style: "stroke:var(--ink-2);opacity:0", "stroke-width": 1 });
    const dot = el("circle", { r: 4, style: `fill:${color};stroke:var(--surface);opacity:0`, "stroke-width": 2 });
    svg.append(guide, dot);
    box.replaceChildren(svg);
    return { x, y, guide, dot };
  }

  function bindLineHover(box, ch, series, fmtVal) {
    if (!ch) return;
    bindHover(box, series.length, ch.x, (i, cx, top) => {
      const px = ch.x(i), py = ch.y(series[i][1]);
      ch.guide.setAttribute("x1", px); ch.guide.setAttribute("x2", px); ch.guide.style.opacity = 0.35;
      ch.dot.setAttribute("cx", px); ch.dot.setAttribute("cy", py); ch.dot.style.opacity = 1;
      showTip(`<b>${fmtVal(series[i][1])}</b> · ${longDate(series[i][0])}`, cx, top + py);
    }, () => { ch.guide.style.opacity = 0; ch.dot.style.opacity = 0; });
  }

  // 막대 차트. signed=true 면 0 기준 위(빨강)/아래(파랑), 아니면 중립색
  function barChart(box, points, { signed, fmtVal, label, dateFmt = longDate }) {
    const w = box.clientWidth, h = box.clientHeight;
    if (!w || !points.length) return;
    const n = points.length, gap = n > 30 ? 1 : 2;
    const bw = Math.max(1, (w - gap * (n - 1)) / n);
    const vals = points.map((p) => p[1]);
    const hi = Math.max(0, ...vals), lo = signed ? Math.min(0, ...vals) : 0;
    const range = hi - lo || 1;
    const y = (v) => ((hi - v) / range) * h;
    const y0 = y(0);
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": `${label} 막대 차트` });
    const bars = points.map((p, i) => {
      const top = Math.min(y(p[1]), y0), bh = Math.max(1, Math.abs(y(p[1]) - y0));
      const fill = !signed ? "var(--muted)" : p[1] > 0 ? "var(--up)" : p[1] < 0 ? "var(--down)" : "var(--flat)";
      const r = el("rect", { x: i * (bw + gap), y: top, width: bw, height: bh, rx: Math.min(2, bw / 2), style: `fill:${fill};opacity:${signed ? 0.85 : 0.5}` });
      svg.append(r);
      return r;
    });
    svg.append(el("line", { x1: 0, x2: w, y1: y0, y2: y0, style: "stroke:var(--axis)", "stroke-width": 1 }));
    box.replaceChildren(svg);
    const cx = (i) => i * (bw + gap) + bw / 2;
    let on = -1;
    const base = signed ? 0.85 : 0.5;
    bindHover(box, n, cx, (i, clientX, top) => {
      if (on >= 0) bars[on].style.opacity = base;
      bars[i].style.opacity = 1; on = i;
      showTip(`<b>${fmtVal(points[i][1])}</b> · ${dateFmt(points[i][0])}`, clientX, top + Math.min(y(points[i][1]), y0));
    }, () => { if (on >= 0) bars[on].style.opacity = base; on = -1; });
  }

  // ---------- 카드 ----------
  const charts = []; // 그려야 할 차트: [box, drawFn]

  function cardShell(id, ind, cls = "") {
    const card = document.createElement("article");
    card.className = `card ${cls}`.trim();
    const name = ind?.name || NAMES[id] || id;
    card.innerHTML = `
      <div class="card-head">
        <h3>${esc(name)}${ind?.stale ? '<span class="stale">지연</span>' : ""}</h3>
        <span class="asof">${ind ? esc(shortDate(ind.asof, ind.freq)) : ""}</span>
      </div>`;
    return card;
  }

  function emptyCard(id) {
    const card = cardShell(id, null, WIDE.has(id) ? "span-2" : "");
    card.insertAdjacentHTML("beforeend", `<div class="empty">데이터 준비 중이에요</div>`);
    return card;
  }

  function seriesCard(id, ind) {
    const s = ind.series, last = s[s.length - 1][1], prev = s.length > 1 ? s[s.length - 2][1] : last;
    const dp = ind.decimals;
    const diff = last - prev;
    const card = cardShell(id, ind, WIDE.has(id) ? "span-2" : "");
    const fmtVal = (v) => plainUnit(fmt(v, dp), ind.unit);

    let valueHtml, deltaHtml, sub = "";
    if (ind.freq === "W") {
      // 주간 지수: 전주 대비 변동률을 주인공으로
      const pct = (last / prev - 1) * 100, d = dirOf(pct, 2);
      valueHtml = `<span class="${d}-t">${sign(+pct.toFixed(2))}${fmt(Math.abs(pct), 2)}<small class="${d}-t">%</small></span>`;
      deltaHtml = `<span class="lbl">전주 대비</span><span class="${d}-t">${ARROW[d]}</span>`;
      sub = `지수 ${fmt(last, 2)}`;
    } else {
      const d = dirOf(diff, dp);
      const shown = id === "fed" && ind.lower != null ? `${fmt(ind.lower, 2)}~${fmt(last, 2)}` : fmt(last, dp);
      valueHtml = withUnit(shown, ind.unit);
      if (d === "flat") {
        deltaHtml = `<span class="lbl">${PERIOD[ind.freq]}</span><span class="flat-t">변동 없음</span>`;
      } else if (ind.change === "pp") {
        deltaHtml = `<span class="lbl">${PERIOD[ind.freq]}</span><span class="${d}-t">${ARROW[d]} ${fmt(Math.abs(diff), dp)}%p</span>`;
      } else {
        const pct = prev ? (diff / prev) * 100 : 0;
        const absTxt = COUNT_UNITS.has(ind.unit) ? `${fmt(Math.abs(diff), 0)}${ind.unit}` : plainUnit(fmt(Math.abs(diff), dp), ind.unit.startsWith("$") ? "$" : "");
        deltaHtml = `<span class="lbl">${PERIOD[ind.freq]}</span><span class="${d}-t">${ARROW[d]} ${absTxt} (${sign(pct)}${fmt(Math.abs(pct), 2)}%)</span>`;
      }
    }

    const bars = ind.freq === "W" || BAR_IDS.has(id);
    card.insertAdjacentHTML("beforeend", `
      <div class="value">${valueHtml}</div>
      <div class="delta">${deltaHtml}</div>
      ${sub ? `<div class="sub">${sub}</div>` : ""}
      <div class="chart${bars ? " tall" : ""}"></div>
      ${ind.news && !ind.stale ? newsLine(ind.news) : ""}
      ${NOTES[id] ? `<div class="note">${NOTES[id]}</div>` : ""}
      <div class="card-foot"><span>${ind.freq === "W" ? `주간 변동률 · ${spanLabel(s, "W")}` : spanLabel(s, ind.freq)}</span><span>${esc(ind.source)}</span></div>`);

    const box = $(".chart", card);
    if (ind.freq === "W") {
      const pts = s.slice(1).map((p, i) => [p[0], (p[1] / s[i][1] - 1) * 100]).slice(-WEEKS_SHOWN);
      charts.push(() => barChart(box, pts, {
        signed: true, label: ind.name,
        fmtVal: (v) => `${sign(+v.toFixed(2))}${fmt(Math.abs(v), 2)}%`,
        dateFmt: (d) => `${longDate(d)} 주`,
      }));
    } else if (BAR_IDS.has(id)) {
      charts.push(() => barChart(box, s.slice(-24), { signed: false, label: ind.name, fmtVal }));
    } else {
      charts.push(() => bindLineHover(box, lineChart(box, s, ind.name), s, fmtVal));
    }
    return card;
  }

  function flowCard(id, ind) {
    const rows = ind.rows, last = rows[rows.length - 1];
    const card = cardShell(id, ind, "span-2");
    const who = [["개인", "personal"], ["외국인", "foreign"], ["기관", "institutional"]];
    const max = Math.max(1, ...who.map(([, k]) => Math.abs(last[k] || 0)));
    const sum = (k) => rows.some((r) => r[k] == null) ? null : rows.reduce((a, r) => a + r[k], 0);
    const line = ([label, k]) => {
      const v = last[k];
      if (v == null) return `<div class="flow-row"><span class="who">${label}</span><span class="dbar"></span><span class="num">—</span></div>`;
      const d = dirOf(v, 0), pct = (Math.abs(v) / max) * 50;
      const pos = v >= 0 ? `left:50%;width:${pct}%` : `left:${50 - pct}%;width:${pct}%`;
      const col = d === "up" ? "var(--up)" : d === "down" ? "var(--down)" : "var(--flat)";
      return `<div class="flow-row"><span class="who">${label}</span><span class="dbar"><i style="${pos};background:${col}"></i></span><span class="num ${d}-t">${eok(v)}</span></div>`;
    };
    card.insertAdjacentHTML("beforeend", `
      <div class="flow-rows">${who.map(line).join("")}</div>
      <div class="flow-cum"><span>최근 ${rows.length}거래일 누적</span>
        ${who.map(([label, k]) => { const v = sum(k); return `<span>${label} <b class="${v == null ? "" : dirOf(v, 0) + "-t"}">${eok(v)}</b></span>`; }).join("")}
      </div>
      <div class="sub" style="margin-top:10px">외국인 일별 순매수</div>
      <div class="chart"></div>
      <div class="card-foot"><span>순매수(+) · 순매도(−)</span><span>${esc(ind.source)}</span></div>`);
    const box = $(".chart", card);
    const pts = rows.map((r) => [r.date, r.foreign]);
    charts.push(() => barChart(box, pts, { signed: true, label: "외국인 일별 순매수", fmtVal: eok }));
    return card;
  }

  // 크게 움직인 날의 대표 뉴스 한 줄 (누르면 원문)
  function newsLine(n) {
    const t = new Date(n.date);
    const when = isNaN(t) ? "" : ` · ${t.getMonth() + 1}.${t.getDate()} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
    const safe = /^https?:\/\//.test(n.link || "") ? n.link : "#";
    return `<a class="news" href="${esc(safe)}" target="_blank" rel="noopener noreferrer">
      <span class="news-t">${esc(n.title)}</span><span class="news-s">${esc(n.source)}${when}</span></a>`;
  }

  // ---------- 수도권 지도 + TOP10 ----------
  function loadGeo() {
    geoLoading ||= fetch("data/geo/metro.json")
      .then((r) => r.json())
      .then((g) => { GEO = g; if (tab === "re") render(); })
      .catch(() => { geoLoading = null; });
    return geoLoading;
  }

  function colorScale(values) {
    const maxAbs = Math.max(0, ...values.map(Math.abs));
    const domain = Math.max(0.1, Math.ceil(maxAbs * 20) / 20); // 0.05%p 단위로 올림
    const color = (v) => {
      if (v == null) return null;
      if (+v.toFixed(2) === 0) return "var(--map-flat)";
      const i = Math.min(4, Math.floor((Math.abs(v) / domain) * 5));
      return (v > 0 ? RED : BLUE)[i];
    };
    const ink = (v) => (v != null && Math.min(4, Math.floor((Math.abs(v) / domain) * 5)) >= 3 && +v.toFixed(2) !== 0 ? "#fff" : "#1a1a19");
    return { domain, color, ink };
  }

  // 화면 폭에 맞춰 글자 크기를 정하고, 겹치거나 지역 밖으로 넘치는 라벨은 작은 지역부터 숨긴다.
  // 숨긴 지역 값은 탭/호버 툴팁과 TOP10 목록에서 볼 수 있다.
  function placeMapLabels(svg) {
    const { width, height } = svg.getBoundingClientRect();
    if (!width) return;
    const vb = svg.viewBox.baseVal;
    const k = height ? Math.min(width / vb.width, height / vb.height) : width / vb.width; // 높이 제한(전국 지도)도 반영
    const wide = width >= 480;
    const pctPx = wide ? 11.5 : 9, namePx = wide ? 10.5 : 8.5;
    const area = {};
    svg.querySelectorAll("path[data-k]").forEach((p) => { const b = p.getBBox(); area[p.dataset.k] = b; });
    const hasName = !!svg.querySelector(".mn");
    svg.querySelectorAll(".labels text").forEach((t) => {
      const name = t.classList.contains("mn");
      const ly = +t.dataset.y;
      t.style.fontSize = `${(name ? namePx : pctPx) / k}px`;
      t.setAttribute("y", hasName ? (name ? ly - 1.5 / k : ly + (pctPx + 1) / k) : ly + (pctPx * 0.35) / k);
    });
    const items = [...svg.querySelectorAll(".labels text")].map((t) => ({
      t, rect: t.getBoundingClientRect(), box: area[t.dataset.k], name: t.classList.contains("mn"),
    }));
    // 숫자 먼저, 큰 지역 먼저
    items.sort((a, b) => (a.name - b.name) || (b.box.width * b.box.height - a.box.width * a.box.height));
    const kept = [], shownPct = new Set();
    const hit = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5
      && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
    const frame = svg.getBoundingClientRect();
    for (const it of items) {
      const inside = it.rect.left >= frame.left && it.rect.right <= frame.right;
      const fits = inside && it.rect.width <= it.box.width * k * 1.15;
      const ok = fits && !kept.some((o) => hit(o.rect, it.rect)) && (!it.name || shownPct.has(it.t.dataset.k));
      if (ok) { kept.push(it); if (!it.name) shownPct.add(it.t.dataset.k); } else it.t.remove();
    }
    const ctx = svg.querySelector(".ctx"), cl = svg.querySelector(".ctx-label");
    if (ctx && cl) {
      const b = ctx.getBBox();
      cl.style.fontSize = `${(wide ? 13 : 10) / k}px`;
      cl.setAttribute("x", b.x + b.width / 2);
      cl.setAttribute("y", b.y + b.height / 2);
    }
  }

  function topList(items, kind, n) {
    if (!items.length) return `<div class="rank-none">${kind === "up" ? "상승" : "하락"} 지역 없음</div>`;
    const max = Math.max(0.01, ...items.map((i) => Math.abs(i.change)));
    return `<ol>${items.slice(0, n).map((it, i) => `
      <li><span class="no">${i + 1}</span>
        <span class="nm"><span>${esc(it.name)}</span><i style="width:${(Math.abs(it.change) / max) * 100}%;background:var(--${kind})"></i></span>
        <span class="pc ${kind}-t">${pctText(it.change)}</span></li>`).join("")}</ol>`;
  }

  function mapCard() {
    const ind = DATA.indicators[rankKind === "sale" ? "re_rank_sale" : "re_rank_jeonse"];
    const card = document.createElement("article");
    card.className = "card span-all map-card";
    if (!ind?.map) {
      card.innerHTML = `<div class="empty">데이터 준비 중이에요</div>`;
      return card;
    }
    if (!GEO) {
      loadGeo();
      card.innerHTML = `<div class="empty">지도를 불러오는 중…</div>`;
      return card;
    }
    const geo = GEO[mapView];
    const list = ind.lists[mapView];
    if (!geo || !list) {
      card.innerHTML = `<div class="empty">다음 업데이트부터 볼 수 있어요</div>`;
      return card;
    }
    const value = (r) => ind.map[r.key];
    const scale = colorScale(geo.regions.map(value).filter((v) => v != null));
    const named = mapView !== "metro"; // 전국·서울은 지역 이름도 지도에 적는다
    const totals = ({ nation: ["전국"], seoul: ["서울"], metro: ["경기", "인천"] }[mapView]).map((n) => {
      const v = ind.totals[n];
      if (v == null) return "";
      const d = dirOf(v, 2);
      return `<span>${n} <b class="${d}-t">${ARROW[d]} ${pctText(v)}</b></span>`;
    }).join("");
    const label = (r) => (mapView === "seoul" ? r.name.replace(/구$/, "") : mapView === "nation" ? r.name : gyeonggiName(r.name));
    const paths = geo.regions.map((r) => {
      const v = value(r);
      return `<path d="${r.d}" data-k="${esc(r.key)}" style="fill:${scale.color(v) || "var(--map-none)"}"/>`;
    }).join("");
    const labels = geo.regions.map((r) => {
      const v = value(r);
      if (v == null) return "";
      const attrs = `x="${r.lx}" data-k="${esc(r.key)}" data-y="${r.ly}" style="fill:${scale.ink(v)}"`;
      return `<text ${attrs} class="mp">${pctText(v)}</text>`
        + (named ? `<text ${attrs} class="mn">${esc(label(r))}</text>` : "");
    }).join("");
    const legend = [...BLUE].reverse().concat(["var(--map-flat)"], RED)
      .map((c) => `<i style="background:${c}"></i>`).join("");

    card.innerHTML = `
      <div class="map-top">
        <div class="seg" role="group" aria-label="지역 선택">${Object.entries(MAP_VIEWS).map(([k, v]) => `
          <button type="button" data-view="${k}" aria-pressed="${mapView === k}">${v}</button>`).join("")}
        </div>
        <div class="map-tot">${totals}</div>
      </div>
      <div class="sub">${esc(shortDate(ind.asof, "W"))} · 전주 대비 ${rankKind === "sale" ? "매매" : "전세"}가격 변동률${ind.stale ? '<span class="stale">지연</span>' : ""}</div>
      <div class="map-body">
        <div class="map-wrap">
          <svg class="map ${mapView}" viewBox="${geo.viewBox}" role="img" aria-label="${MAP_VIEWS[mapView]} ${mapView === "nation" ? "시도" : "시군구"}별 변동률 지도">
            ${geo.context ? `<path d="${geo.context}" class="ctx"/>` : ""}
            <g class="regions">${paths}</g>
            ${geo.context ? `<text class="ctx-label">서울</text>` : ""}
            <g class="labels">${labels}</g>
          </svg>
          <div class="legend"><span>${pctText(-scale.domain)}</span><span class="ramp">${legend}</span><span>${pctText(scale.domain)}</span></div>
        </div>
        <div class="rank-cols">
          <div><h4>상승 TOP10</h4>${topList(list.up, "up", 10)}</div>
          <div><h4>하락 TOP10</h4>${topList(list.down, "down", 10)}</div>
        </div>
      </div>
      <div class="card-foot"><span>${{ nation: "지도 17개 시도 · 순위 전국 시군구", seoul: "서울 25개 구", metro: "경기·인천 시군구(구)" }[mapView]} · ${list.count}개 지역 집계</span><span>${esc(ind.source)} · 경계 통계청</span></div>`;

    const svg = $("svg", card);
    const names = Object.fromEntries(geo.regions.map((r) => [r.key, r]));
    charts.push(() => placeMapLabels(svg));
    let on = null;
    const off = () => { on?.classList.remove("on"); on = null; hideTip(); };
    const point = (e) => {
      const p = e.target.closest("path[data-k]");
      if (!p) return off();
      if (on !== p) { on?.classList.remove("on"); p.classList.add("on"); on = p; }
      const r = names[p.dataset.k], v = ind.map[r.key];
      const nm = r.sido === "인천" ? `인천 ${r.name}` : r.sido === "경기" ? gyeonggiName(r.name) : r.name;  // 전국 지도는 sido 가 "전국"
      showTip(`<b>${esc(nm)}</b> ${v == null ? "데이터 없음" : pctText(v)}`, e.clientX, e.clientY - 6);
    };
    svg.addEventListener("pointermove", point);
    svg.addEventListener("pointerdown", point);
    svg.addEventListener("pointerleave", off);
    return card;
  }

  // ---------- 서울 입주물량 (수기 입력) ----------
  function supplyCard(id, ind) {
    const ys = ind.years;
    const thisYear = new Date().getFullYear();
    const now = ys.find((y) => y.year === thisYear) || ys[ys.length - 1];
    const last = ys[ys.length - 1];
    const status = (y) => (y.year <= ind.actualUntil ? "실적" : "예정");
    const chg = (last.value / now.value - 1) * 100;
    const short = (v) => (v >= 10000 ? `${fmt(v / 10000, 1)}만` : `${fmt(v / 1000, 1)}천`);
    const card = cardShell(id, ind, "span-2");
    $(".asof", card).textContent = `${longDate(ind.asof)} 기준`;
    card.insertAdjacentHTML("beforeend", `
      <div class="value">${fmt(now.value, 0)}<small>가구</small></div>
      <div class="delta"><span class="lbl">${now.year}년 ${status(now)}</span>
        <span class="flat-t">→ ${last.year}년 ${fmt(last.value, 0)}가구 (${sign(chg)}${fmt(Math.abs(chg), 0)}%)</span></div>
      <div class="chart supply"></div>
      <div class="supply-key"><span><i class="k-act"></i>실적</span><span><i class="k-plan"></i>예정·전망</span>
        <span class="hint">${thisYear}년 이후 막대를 누르면 입주 단지 목록</span></div>
      ${supplyYear ? complexList(ind, supplyYear) : ""}
      <div class="note">${esc(ind.source)}. 막대 값은 자동 수집이 아니라 직접 입력해 갱신하는 값이에요.</div>
      <div class="card-foot"><span>${ys[0].year}–${last.year}년</span><span>부동산R114${ind.complexes?.some((c) => c.auto) ? " · 단지 청약홈" : ""}</span></div>`);
    const box = $(".chart", card);
    charts.push(() => {
      const w = box.clientWidth, h = box.clientHeight;
      if (!w) return;
      const top = 18, bottom = 18, n = ys.length, gap = 6;
      const bw = (w - gap * (n - 1)) / n;
      const max = Math.max(...ys.map((y) => y.value));
      const y = (v) => top + (1 - v / max) * (h - top - bottom);
      const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": "서울 아파트 연도별 입주물량 막대 차트" });
      const bars = ys.map((d, i) => {
        const x = i * (bw + gap), act = d.year <= ind.actualUntil;
        const r = el("rect", { x, y: y(d.value), width: bw, height: h - bottom - y(d.value), rx: 3,
          class: `${act ? "s-act" : "s-plan"}${d.year === supplyYear ? " sel" : ""}` });
        const t = el("text", { x: x + bw / 2, y: y(d.value) - 5, class: "s-val" });
        t.textContent = short(d.value);
        const yl = el("text", { x: x + bw / 2, y: h - 4, class: "s-yr" });
        yl.textContent = `'${String(d.year).slice(2)}`;
        svg.append(r, t, yl);
        return r;
      });
      svg.append(el("line", { x1: 0, x2: w, y1: h - bottom, y2: h - bottom, style: "stroke:var(--axis)" }));
      box.replaceChildren(svg);
      let on = -1;
      const cx = (i) => i * (bw + gap) + bw / 2;
      bindHover(box, n, cx, (i, clientX, boxTop) => {
        if (on >= 0) bars[on].classList.remove("on");
        bars[i].classList.add("on"); on = i;
        const d = ys[i];
        showTip(`<b>${d.year}년 ${fmt(d.value, 0)}가구</b> · ${status(d)}<br>${esc(d.source)}`, clientX, boxTop + y(d.value));
      }, () => { if (on >= 0) bars[on].classList.remove("on"); on = -1; });
      // 올해 이후 막대를 누르면 그해 입주 단지 목록을 펼치고, 다시 누르면 닫는다
      box.addEventListener("click", (e) => {
        const x = e.clientX - box.getBoundingClientRect().left;
        const i = ys.reduce((best, _, j) => (Math.abs(cx(j) - x) < Math.abs(cx(best) - x) ? j : best), 0);
        const yr = ys[i].year;
        if (yr < thisYear) return;
        supplyYear = supplyYear === yr ? null : yr;
        render();
      });
    });
    return card;
  }

  function complexList(ind, year) {
    const list = (ind.complexes || []).filter((c) => +c.ym.slice(0, 4) === year);
    const total = list.reduce((a, c) => a + (c.units || 0), 0);
    const head = `<div class="cx-head"><b>${year}년 입주 예정 단지</b><span>${list.length}개${list.length ? ` · ${fmt(total, 0)}세대` : ""}</span></div>`;
    if (!list.length) {
      return `<div class="cx">${head}<div class="rank-none">${ind.complexesAuto
        ? "등록된 단지가 없어요. 아직 분양 전인 단지가 많아요."
        : "단지 목록은 청약홈 키를 등록하면 자동으로 채워져요."}</div></div>`;
    }
    return `<div class="cx">${head}<ol>${list.map((c) => `
      <li><span class="gu">${esc(c.gu)}</span><span class="nm">${esc(c.name)}</span>
        <span class="un">${fmt(c.units, 0)}세대</span><span class="ym">${esc(longDate(c.ym))}</span></li>`).join("")}</ol>
      <div class="note">세대수는 청약홈 모집공고의 공급(분양) 세대 기준이라 조합원·임대분이 빠져 막대 값보다 작아요.</div></div>`;
  }

  // ---------- 서울 아파트 매물 수 (네이버 부동산, 날짜별로 쌓음) ----------
  function listingCard(id, ind) {
    const card = cardShell(id, ind, "span-2");
    const H = ind.history;
    const gus = Object.keys(H).filter((g) => g !== "서울").sort((a, b) => a.localeCompare(b, "ko"));
    if (!H[listGu]) listGu = "서울";
    const k = listKind + 1; // 행: [날짜, 매매, 전세, 월세]
    const rows = H[listGu];
    const last = rows[rows.length - 1];
    // 비교 기준: 30일 전과 가장 가까운 기록 (기록이 짧으면 가장 오래된 기록)
    const baseOf = (rs) => {
      const target = new Date(rs[rs.length - 1][0]); target.setDate(target.getDate() - 30);
      const t = target.toISOString().slice(0, 10);
      return [...rs].reverse().find((r) => r[0] <= t) || rs[0];
    };
    const base = baseOf(rows);
    const days = Math.round((new Date(last[0]) - new Date(base[0])) / 864e5);
    const diff = last[k] - base[k];
    const d = dirOf(diff, 0);
    const pct = base[k] ? (diff / base[k]) * 100 : 0;
    const deltaHtml = days
      ? `<span class="lbl">${days}일 전 대비</span><span class="${d}-t">${ARROW[d]} ${fmt(Math.abs(diff), 0)}건 (${sign(+pct.toFixed(1))}${fmt(Math.abs(pct), 1)}%)</span>`
      : `<span class="lbl">추이는 오늘부터 하루씩 쌓여요</span>`;

    // 구별 증감 목록
    const items = gus.map((g) => {
      const rs = H[g], b = baseOf(rs), l = rs[rs.length - 1];
      return { g, now: l[k], chg: b[k] ? (l[k] / b[k] - 1) * 100 : 0 };
    }).sort((a, b) => (days ? b.chg - a.chg : b.now - a.now));
    const maxAbs = Math.max(0.1, ...items.map((i) => Math.abs(i.chg)));

    card.insertAdjacentHTML("beforeend", `
      <div class="lst-top">
        <div class="seg" role="group" aria-label="거래 종류">${LIST_KINDS.map(([, v], i) => `
          <button type="button" data-lkind="${i}" aria-pressed="${listKind === i}">${v}</button>`).join("")}</div>
        <select class="lst-gu" aria-label="지역 선택">
          <option value="서울"${listGu === "서울" ? " selected" : ""}>서울 전체</option>
          ${gus.map((g) => `<option${g === listGu ? " selected" : ""}>${esc(g)}</option>`).join("")}
        </select>
      </div>
      <div class="value">${fmt(last[k], 0)}<small>건</small></div>
      <div class="delta">${deltaHtml}</div>
      <div class="sub">${esc(listGu === "서울" ? "서울 전체" : listGu)} ${LIST_KINDS[listKind][1]} 매물</div>
      ${rows.length > 1 ? `<div class="chart"></div>` : ""}
      <details class="lst"${listOpen ? " open" : ""}><summary>구별 ${days ? `${days}일 증감` : "매물 수"} · 눌러서 선택</summary>
        <ol>${items.map((it) => {
          const dd = dirOf(it.chg, 1);
          return `<li data-gu="${esc(it.g)}"${it.g === listGu ? ' class="sel"' : ""}><span class="nm">${esc(it.g)}</span>
            <span class="gbar">${days ? `<i style="width:${(Math.abs(it.chg) / maxAbs) * 100}%;background:var(--${dd === "flat" ? "flat" : dd})"></i>` : ""}</span>
            <span class="pc">${fmt(it.now, 0)}</span>
            ${days ? `<span class="pc ${dd}-t">${sign(+it.chg.toFixed(1))}${fmt(Math.abs(it.chg), 1)}%</span>` : ""}</li>`;
        }).join("")}</ol></details>
      <div class="note">네이버 부동산에 올라온 아파트 매물 수예요(같은 집을 여러 중개사가 올리면 중복돼요). 비공식 수집이라 가끔 빠질 수 있어요.</div>
      <div class="card-foot"><span>${esc(longDate(rows[0][0]))}부터 기록</span><span>${esc(ind.source)}</span></div>`);

    const box = $(".chart", card);
    if (box) {
      const s = rows.map((r) => [r[0], r[k]]);
      charts.push(() => bindLineHover(box, lineChart(box, s, `${listGu} 매물`), s, (v) => `${fmt(v, 0)}건`));
    }
    $(".lst-gu", card).addEventListener("change", (e) => { listGu = e.target.value; render(); });
    $("details.lst", card).addEventListener("toggle", (e) => { listOpen = e.target.open; });
    return card;
  }

  // 미국 11개 섹터 ETF 등락: 오른 순서대로 막대, 점선은 S&P 500
  function sectorCard(id, ind) {
    const p = sectorPeriod, bench = ind.bench?.chg?.[p];
    const rows = ind.rows.filter((r) => r.chg[p] != null).sort((a, b) => b.chg[p] - a.chg[p]);
    const max = Math.max(0.1, ...rows.map((r) => Math.abs(r.chg[p])), Math.abs(bench ?? 0));
    const pos = (v) => 50 + (v / max) * 50;
    const ups = rows.filter((r) => dirOf(r.chg[p]) === "up").length;
    const downs = rows.filter((r) => dirOf(r.chg[p]) === "down").length;
    const card = cardShell(id, ind, "span-2");
    card.insertAdjacentHTML("beforeend", `
      <div class="lst-top">
        <div class="seg" role="group" aria-label="기간 선택">${Object.entries(SECTOR_PERIODS).map(([k, v]) => `
          <button type="button" data-speriod="${k}" aria-pressed="${p === k}">${v}</button>`).join("")}</div>
      </div>
      <div class="flow-cum sec-sum">
        <span>오른 업종 <b class="up-t">${ups}</b></span><span>내린 업종 <b class="down-t">${downs}</b></span>
        ${bench != null ? `<span><i class="bm-key"></i>S&amp;P 500 <b class="${dirOf(bench)}-t">${pctText(bench)}</b></span>` : ""}
      </div>
      <div class="sec-rows">${rows.map((r) => {
        const v = r.chg[p], d = dirOf(v);
        const col = d === "up" ? "var(--up)" : d === "down" ? "var(--down)" : "var(--flat)";
        const bar = v >= 0 ? `left:50%;width:${pos(v) - 50}%` : `left:${pos(v)}%;width:${50 - pos(v)}%`;
        return `<div class="sec-row"><span class="nm">${esc(r.name)}<small>${esc(r.sym)}</small></span>
          <span class="dbar"><i style="${bar};background:${col}"></i>${bench != null ? `<b class="bm" style="left:${pos(bench)}%"></b>` : ""}</span>
          <span class="num ${d}-t">${pctText(v)}</span></div>`;
      }).join("")}</div>
      <div class="note">미국 장 마감(한국시간 새벽 5~6시) 종가 기준이에요. 점선은 S&amp;P 500(SPY) 등락이라, 점선보다 오른쪽이면 시장보다 강했던 업종이에요.</div>
      <div class="card-foot"><span>${SECTOR_PERIODS[p]} 등락률</span><span>${esc(ind.source)}</span></div>`);
    return card;
  }

  // ---------- 렌더 ----------
  function render() {
    charts.length = 0;
    hideTip();
    const frag = document.createDocumentFragment();
    for (const group of LAYOUT[tab]) {
      const sec = document.createElement("section");
      sec.className = "group";
      const isMap = group.ids[0] === "re_map";
      sec.innerHTML = `<div class="group-head"><h2>${esc(group.title)}</h2>${isMap ? `
        <div class="seg" role="group" aria-label="매매/전세 선택">
          <button type="button" data-kind="sale" aria-pressed="${rankKind === "sale"}">매매</button>
          <button type="button" data-kind="jeonse" aria-pressed="${rankKind === "jeonse"}">전세</button>
        </div>` : ""}</div>`;
      const grid = document.createElement("div");
      grid.className = "grid";
      for (const id of group.ids) {
        const ind = DATA.indicators[id];
        grid.append(
          id === "re_map" ? mapCard()
          : !ind ? emptyCard(id)
          : ind.type === "flow" ? flowCard(id, ind)
          : ind.type === "supply" ? supplyCard(id, ind)
          : ind.type === "listing" ? listingCard(id, ind)
          : ind.type === "sectors" ? sectorCard(id, ind)
          : seriesCard(id, ind));
      }
      sec.append(grid);
      frag.append(sec);
    }
    main.replaceChildren(frag);
    charts.forEach((draw) => draw());
  }

  function setTab(next, push = true) {
    if (!LAYOUT[next]) next = "kr";
    tab = next;
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === tab));
    store.set("tab", tab);
    if (push) history.replaceState(null, "", `#${tab}`);
    if (DATA) render();
  }

  function renderMeta() {
    const u = new Date(DATA.updated);
    const pad = (n) => String(n).padStart(2, "0");
    $("#updated").textContent = isNaN(u)
      ? ""
      : `${u.getFullYear()}.${pad(u.getMonth() + 1)}.${pad(u.getDate())} ${pad(u.getHours())}:${pad(u.getMinutes())} 업데이트`;
    const errs = DATA.errors || [];
    if (errs.length) {
      const foot = $(".foot");
      foot.querySelector(".errors")?.remove();
      foot.insertAdjacentHTML("beforeend", `<details class="errors"><summary>이번 업데이트에서 못 받아온 지표 ${errs.length}개</summary><ul>${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>`);
    }
  }

  // ---------- 이벤트 ----------
  document.querySelector(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (b) { setTab(b.dataset.tab); scrollTo({ top: 0 }); }
  });
  main.addEventListener("click", (e) => {
    const b = e.target.closest(".seg button[data-kind]");
    if (b && b.dataset.kind !== rankKind) { rankKind = b.dataset.kind; render(); }
    const v = e.target.closest(".seg button[data-view]");
    if (v && v.dataset.view !== mapView) { mapView = v.dataset.view; render(); }
    const lk = e.target.closest(".seg button[data-lkind]");
    if (lk && +lk.dataset.lkind !== listKind) { listKind = +lk.dataset.lkind; render(); }
    const sp = e.target.closest(".seg button[data-speriod]");
    if (sp && sp.dataset.speriod !== sectorPeriod) { sectorPeriod = sp.dataset.speriod; render(); }
    const g = e.target.closest("li[data-gu]");
    if (g) { listGu = g.dataset.gu === listGu ? "서울" : g.dataset.gu; render(); }
  });
  addEventListener("hashchange", () => setTab(location.hash.slice(1), false));
  let resizeTimer, lastW = innerWidth;
  addEventListener("resize", () => {
    if (innerWidth === lastW) return; // 모바일 주소창 높이 변화는 무시
    lastW = innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => DATA && render(), 150);
  });
  addEventListener("scroll", hideTip, { passive: true });

  const themeSaved = store.get("theme");
  if (themeSaved) document.documentElement.dataset.theme = themeSaved;
  $("#themeBtn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
  });

  // ---------- 시작 ----------
  setTab(location.hash.slice(1) || store.get("tab") || "kr", false);
  fetch(`data/data.json?v=${Date.now()}`)
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => { DATA = d; renderMeta(); render(); })
    .catch(() => {
      main.innerHTML = `<div class="empty">데이터를 불러오지 못했어요. 잠시 후 새로고침해 주세요.</div>`;
      $("#updated").textContent = "";
    });
})();
