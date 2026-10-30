# 경제지표 대시보드

한국주식 · 미국주식 · 부동산 주요 지표를 하루 두 번(07:10 · 18:30 KST) 자동으로 모아 보여주는 웹 대시보드입니다.
아침 실행은 전날 밤 미국장 마감을, 저녁 실행은 그날 한국장 마감을 반영해요.
GitHub Pages로 호스팅되고, GitHub Actions가 매일 데이터를 갱신합니다.

## 보여주는 지표

| 탭 | 지표 | 출처 |
|---|---|---|
| 공통 | 크게 움직인 날(지수 ±1.5%, 종목 ±3% 등) 카드 아래 대표 뉴스 헤드라인 한 줄 | 구글 뉴스 RSS (키 불필요) |
| 한국주식 | 코스피, 코스닥, 원·달러 환율 | Yahoo Finance (실패 시 한국은행 ECOS) |
| | 삼성전자, SK하이닉스 | Yahoo Finance |
| | 코스피 투자자별 순매수 (개인·외국인·기관, 최근 20일) | 네이버 증권 (실패 시 ECOS 외국인만) |
| | 한국 기준금리, 국고채 3년·10년 | 한국은행 ECOS |
| 미국주식 | S&P 500, 나스닥, 다우, VIX, 달러인덱스, 금(COMEX 선물) | Yahoo Finance (실패 시 FRED) |
| | 업종별 등락: S&P 500 11개 섹터 ETF(XLK·XLF 등) 1일·1주·1개월·연초 이후 | Yahoo Finance |
| | 미국 기준금리, 미국채 2년·10년·30년, 장단기 금리차 | FRED |
| | 일본 국채 3년·10년 / 독일 국채 3년·10년 | 일본 재무성 / 독일 분데스방크 (키 불필요) |
| | 미국 CPI(전년 대비), 실업률 | FRED |
| 부동산 | 전국 아파트 매매·전세가격지수 주간 변동률 | 한국부동산원 R-ONE |
| | 주간 변동률 지도 (전국 17개 시도 / 서울 / 경기·인천) + 상승·하락 TOP10 (매매/전세 전환) | R-ONE, 경계: 통계청(vuski/admdongkor) |
| | 서울 아파트 매물 수 (매매·전세·월세, 25개 구) — 매일 저녁 쌓아서 추이 표시 | 네이버 부동산 (비공식, 막히면 '지연' 표시) |
| | 서울 아파트 입주물량 2020–2030 + 연도별 입주 예정 단지 | 막대: **수기 입력** (부동산R114 보도 기준) / 단지: 청약홈 + 수기 보충 |
| | 서울 아파트 매매거래량, 주택담보대출 금리 | R-ONE, 한국은행 ECOS |

### 직접 고치는 데이터
- **서울 입주물량**: `data/manual/seoul_supply.json`의 `years` 값을 고쳐서 올리면 다음 실행 때 반영됩니다.
  `actual_until` 이하 연도는 '실적', 그 뒤는 '예정'으로 표시돼요.
- **입주 예정 단지**: 청약홈에 없는 단지(조합원 위주 재건축 등)는 같은 파일의 `complexes`에
  `{"gu": "성북구", "name": "단지명", "units": 1200, "ym": "2027-03"}` 형식으로 추가하면 돼요.
- **지도 경계**: 행정구역이 바뀌면 [vuski/admdongkor](https://github.com/vuski/admdongkor)의 최신 행정동 GeoJSON을 받아
  `pip install shapely` 후 `python scripts/build_map.py <파일>`로 `data/geo/metro.json`을 다시 만듭니다.

## 처음 설정 (한 번만)

### 1. API 키 발급 (모두 무료)

| 이름 | 발급처 |
|---|---|
| `ECOS_API_KEY` | [한국은행 ECOS Open API](https://ecos.bok.or.kr/api/) → 인증키 신청 |
| `FRED_API_KEY` | [FRED API Keys](https://fredaccount.stlouisfed.org/apikeys) → 계정 생성 후 Request API Key |
| `REB_API_KEY` | [한국부동산원 R-ONE Open API](https://www.reb.or.kr/r-one/portal/openapi/openApiIntroPage.do) → 인증키 신청 |
| `DATA_GO_KR_KEY` | [공공데이터포털 청약홈 분양정보 조회 서비스](https://www.data.go.kr/data/15098547/openapi.do) → 활용신청 후 마이페이지의 **일반 인증키(Decoding)** |

### 2. 저장소 만들고 파일 올리기

1. GitHub에서 **New repository**를 누르고 이름(예: `econ-dashboard`)을 정한 뒤 **Public**으로 만듭니다.
2. 이 폴더의 파일을 모두 올립니다. `.github` 폴더도 꼭 포함해야 합니다.

### 3. 키 등록

저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서
`ECOS_API_KEY`, `FRED_API_KEY`, `REB_API_KEY`, `DATA_GO_KR_KEY` 네 개를 등록합니다.

### 4. Pages 켜기

**Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 바꿉니다.

### 5. 첫 실행

**Actions 탭 → "데이터 업데이트 & 배포" → Run workflow**를 누릅니다.
초록 체크가 뜨면 `https://<내아이디>.github.io/<저장소이름>/`에서 확인할 수 있어요.

이후에는 매일 07:10, 18:30(KST)쯤 자동으로 갱신됩니다. GitHub 사정에 따라 몇십 분 늦어질 수 있어요.

## 참고

- 특정 탭으로 바로 가는 링크: `…/#kr`, `…/#us`, `…/#re`
- 어떤 지표를 못 받아오면 이전 값을 유지하고 카드에 **지연** 표시가 붙습니다.
  자세한 오류는 페이지 맨 아래에서 볼 수 있어요.
- 지표를 추가하거나 빼려면 `scripts/fetch_data.py`의 `SERIES`와 `assets/app.js`의 `LAYOUT`을 고치면 됩니다.
- 로컬 실행: `python scripts/fetch_data.py` (환경변수로 키 지정) 후 `python -m http.server`
