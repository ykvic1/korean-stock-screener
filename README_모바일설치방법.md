# 한국주식 자동 스크리너 모바일 v1.2

## 무엇이 다른가

안드로이드/아이폰에서 `.bat` 파일을 실행하지 않습니다. **GitHub Actions 클라우드가 장 종료 후 Python을 실행**하고 날짜별 `.xlsx` 파일을 저장합니다. **GitHub Pages 웹사이트**를 스마트폰 브라우저에서 열어 종목 순위, OBV 가격괴리, 120주선, 수급 확인 여부를 확인하고 Excel을 다운로드합니다.

- 예약: 매주 월~금 한국시간 오후 **6시 30분** (GitHub 예약 실행은 몇 분 이상 지연 가능)
- 파일: `docs/reports/한국주식_스크리너_YYYY-MM-DD.xlsx`
- 최신판: `docs/reports/latest.xlsx`
- 모바일 대시보드: GitHub Pages 주소 (설정 후 `https://계정명.github.io/저장소명/`)
- 자동 주문: 없음. 증권계좌 연결: 없음.

## 설치 — 처음 한 번만 GitHub 설정

1. GitHub 계정 생성 또는 로그인: https://github.com
2. **Public** 저장소를 하나 만듭니다. 예: `korean-stock-screener`. 무료 GitHub Pages를 사용하려면 Public 저장소가 필요합니다. **Public 저장소에서는 분석 결과와 파일이 누구에게나 공개됩니다.** 비공개 분석을 원하면 Private 저장소에서 Pages 없이 Actions의 엑셀 artifact만 받는 방식을 사용하세요.
3. 이 ZIP 압축을 해제한 **내용물 전부를 저장소 루트에 업로드**합니다. `.github/workflows/daily.yml` 경로가 반드시 유지되어야 합니다. GitHub 웹에서 폴더가 누락되지 않았는지 확인하세요. `.github`는 숨김 폴더이므로 압축 해제 도구에서 숨김 파일도 포함해야 합니다.
4. 저장소 **Settings → Pages → Build and deployment → Source: GitHub Actions** 선택.
5. **Actions → 한국주식 모바일 자동 스크리너 → Run workflow** 를 눌러 첫 실행합니다.
6. Actions 실행 기록에서 **초록색 ✓** 와 `당일 한국 주식 데이터 분석` 단계 정상 종료를 확인합니다. 실패 시 해당 작업을 열어 에러를 확인하세요. (KRX 인증/FinanceDataReader 구조 변경 시 수집 실패할 수 있습니다.)
7. Pages URL을 모바일에서 열어 홈 화면에 추가하면 앱처럼 사용할 수 있습니다. 주소는 `https://계정명.github.io/저장소명/` 형식이며 GitHub 설정에서 정확한 주소를 확인할 수 있습니다.

> 스마트폰만으로 GitHub 저장소 파일들을 업로드할 수 있지만, `.github` 숨김 폴더와 하위 디렉터리 구조를 유지하는 과정은 PC에서 한 번 설정하는 것이 더 수월할 수 있습니다. 초기 업로드는 최초 1회이며 이후 조회는 폰으로 가능합니다.

## 작동 방식

1. `FinanceDataReader`의 `KRX-MARCAP` 전종목 시세·시가총액·거래대금을 조회
2. 시총 5천억~5조 / 거래대금 5억 원 이상 기준으로 1차 후보 선별
3. 거래대금 기준 상위 160개 종목 과거 가격으로 거래량·OBV·60일 등락·120주선 계산
4. 상위 35개에 한해 `pykrx` 기관/외국인 20일 순매수 조회 시도
5. 점수화한 100개 이내 후보를 엑셀과 모바일 페이지에 저장

기본값 변경: `config.json` 수정. GitHub Actions에서는 다음 실행부터 자동 적용됩니다.

## 중요한 제약

- **2026년 KRX 인증/조회 정책 변경으로 `pykrx`나 FinanceDataReader의 KRX 리스팅 호출이 실패할 수 있습니다.** 수집 실패 시 새 보고서를 작성하지 않습니다. 이전에 성공한 분석 페이지는 남아 있으며 `가격 기준일`이 업데이트되지 않으므로 반드시 확인하세요.
- 해외/공휴일은 정상 종가 자료 갱신 여부를 확인합니다. 그날 장이 쉬었으면 마지막 성공일 파일을 다시 덮어쓸 수 있습니다.
- `pykrx` 수급 API가 조회되지 않으면 외국인·기관 매집 여부는 판단할 수 없습니다. 엑셀과 모바일 페이지에 **수급 미확인**으로 표시합니다.
- PBR·최근 실적·매출/시총·현금흐름·엘리어트 카운팅은 수집/자동판정 대상이 아니며 임의로 추정하지 않습니다.
- 데이터 수집은 서비스 정책 및 응답 구조에 의존합니다. 이 ZIP은 **오프라인 코드 검사와 화면/보고서 예제로 검증**했지만 실제 클라우드 자동수집의 성공을 보증하지 않습니다.
- 순위 점수는 상승 확률이나 자동 매수 신호가 아닙니다. 투자 판단에 사용하기 전 검증 필요.
- Actions 스케줄은 UTC 기준이고 늦어질 수 있습니다. GitHub가 예약 실행을 중단할 수도 있으므로 `Actions` 기록과 날짜를 확인하세요.
- **Public GitHub Pages는 누구나 볼 수 있습니다.** 본인의 보유주식, 매수가, 개인정보, API 키를 절대 저장소에 넣지 마세요.

## 주요 구성 파일

- `screener.py`: 매일 데이터 수집·점수·엑셀/JSON 생성
- `requirements.txt`: Python 의존성
- `config.json`: 후보 수와 조건
- `.github/workflows/daily.yml`: 매일 KST 18:30 클라우드 자동 실행
- `docs/index.html`: 휴대폰에 최적화한 웹 화면
- `docs/data.json`: 마지막 정상 분석 결과 (첫 실행 전에는 비어 있음)
- `docs/history.json`: 과거 Excel 보고서 링크

## 문제 해결

1. **Pages에 아무 종목도 안 보이면:** `Actions` 마지막 실행이 초록 체크인지 확인하고, `docs/data.json`의 `status`가 success인지 확인합니다.
2. **데이터 날짜가 오래되면:** `Actions`에서 수동 Run workflow 후 에러를 확인합니다. 일반적인 Github Pages 캐시는 새로고침하면 갱신됩니다.
3. **패키지 설치 실패:** 오류 내용을 바탕으로 `requirements.txt`의 버전 호환성을 수정합니다.
4. **수급 미확인:** `pykrx` 응답이 정상인지 확인합니다. 임의로 순매수 값을 채우면 안 됩니다.

### 웹사이트 관련 공식 문서
- GitHub Pages: https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
- Actions Schedule: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- FinanceDataReader: https://github.com/FinanceData/FinanceDataReader
