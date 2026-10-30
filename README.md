# 경제지표 대시보드

한국주식 · 미국주식 · 부동산 주요 지표를 하루 한 번(18:30 KST) 자동으로 모아 보여주는 웹 대시보드입니다.
GitHub Pages로 호스팅되고, GitHub Actions가 매일 데이터를 갱신합니다.

## 보여주는 지표

| 탭 | 지표 | 출처 |
|---|---|---|
| 한국주식 | 코스피, 코스닥, 원·달러 환율 | Yahoo Finance (실패 시 한국은행 ECOS) |
| | 코스피 투자자별 순매수 (개인·외국인·기관, 최근 20일) | 네이버 증권 (실패 시 ECOS 외국인만) |
| | 한국 기준금리, 국고채 3년·10년 | 한국은행 ECOS |
| 미국주식 | S&P 500, 나스닥, 다우, VIX, 달러인덱스 | Yahoo Finance (실패 시 FRED) |
| | 미국 기준금리, 미국채 2년·10년·30년, 장단기 금리차 | FRED |
| | 미국 CPI(전년 대비), 실업률 | FRED |
| 부동산 | 전국 아파트 매매·전세가격지수 주간 변동률 | 한국부동산원 R-ONE |
| | 서울·경기·인천 주간 상승/하락 TOP5 (매매/전세 전환) | 한국부동산원 R-ONE |
| | 서울 아파트 매매거래량, 주택담보대출 금리 | R-ONE, 한국은행 ECOS |

## 처음 설정 (한 번만)

### 1. API 키 발급 (모두 무료)

| 이름 | 발급처 |
|---|---|
| `ECOS_API_KEY` | [한국은행 ECOS Open API](https://ecos.bok.or.kr/api/) → 인증키 신청 |
| `FRED_API_KEY` | [FRED API Keys](https://fredaccount.stlouisfed.org/apikeys) → 계정 생성 후 Request API Key |
| `REB_API_KEY` | [한국부동산원 R-ONE Open API](https://www.reb.or.kr/r-one/portal/openapi/openApiIntroPage.do) → 인증키 신청 |

### 2. 저장소 만들고 파일 올리기

1. GitHub에서 **New repository**를 누르고 이름(예: `econ-dashboard`)을 정한 뒤 **Public**으로 만듭니다.
2. 이 폴더의 파일을 모두 올립니다. `.github` 폴더도 꼭 포함해야 합니다.

### 3. 키 등록

저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서
`ECOS_API_KEY`, `FRED_API_KEY`, `REB_API_KEY` 세 개를 등록합니다.

### 4. Pages 켜기

**Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 바꿉니다.

### 5. 첫 실행

**Actions 탭 → "데이터 업데이트 & 배포" → Run workflow**를 누릅니다.
초록 체크가 뜨면 `https://<내아이디>.github.io/<저장소이름>/`에서 확인할 수 있어요.

이후에는 매일 18:30(KST)쯤 자동으로 갱신됩니다. GitHub 사정에 따라 몇십 분 늦어질 수 있어요.

## 참고

- 특정 탭으로 바로 가는 링크: `…/#kr`, `…/#us`, `…/#re`
- 어떤 지표를 못 받아오면 이전 값을 유지하고 카드에 **지연** 표시가 붙습니다.
  자세한 오류는 페이지 맨 아래에서 볼 수 있어요.
- 지표를 추가하거나 빼려면 `scripts/fetch_data.py`의 `SERIES`와 `assets/app.js`의 `LAYOUT`을 고치면 됩니다.
- 로컬 실행: `python scripts/fetch_data.py` (환경변수로 키 지정) 후 `python -m http.server`
