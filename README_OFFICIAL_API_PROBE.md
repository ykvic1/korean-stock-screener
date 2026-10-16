# KRX + OpenDART 연결 준비 패치 (v2.2 유지, 다음 단계의 사전 준비)

## 목적 및 검증 수준

- **모듈/CLI/테스트/수동 GitHub Actions만 추가**합니다. `screener.py`, `official_data_v22.py`, `session_policy.py`, 기존 워크플로, `docs/` 산출물은 변경하지 않습니다.
- 기본 실행은 **dry-run**입니다. 외부 API를 부르지 않으며 키가 없어도 작동합니다.
- 인증/서비스 이용 승인이 확인된 뒤에만 `--live` 또는 Actions 수동 `mode=live`로 실행합니다.
- KRX API는 KOSPI, KOSDAQ **하루당 최대 각 1회** 요청합니다. API는 날짜별 시장 전 종목을 반환하므로 결과 중 최대 10개 요청 종목만 확인합니다.
- DART는 해당 종목들 중 최대 2개 기업을 표본으로 선택해 고유번호 매핑·공시검색을 시험합니다. 재무제표 1개를 추가 조회할 수 있지만 기본적으로 생략하며, **공시 당시 공개 여부와 수익률 백테스트 입력을 확정하지 않습니다**.
- `ACC_TRDVOL`은 **정규장 09:00~15:30 거래량만이라는 증거가 없는 누적 거래량**으로 보관합니다. 별도 `regular_volume`은 `null`입니다.
- KRX Open API의 주식 일별 데이터에서 개인/외국인/기관/연기금 순매수 분리가 제공된다는 점은 확인되지 않았으므로 **IAS 계산은 계속 차단**합니다.
- 조회 성공≠정규장 세션 검증≠수정주가·기업행위 검증≠투자용 점수 확정.
- API 데이터의 배포/가공 가능 범위는 이용약관/허가 조건을 별도 확인합니다. 수집 결과를 공용 Pages로 게시하지 않습니다.

## 공식 근거

- KRX 주식 일별 API 목록: https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
- KRX 일별매매정보 상세: https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC
- OpenDART 고유번호 API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018
- OpenDART 공시검색 API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001
- OpenDART 재무제표 API: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020
- 현존 프로젝트 문서: `V22_ROADMAP.md`, `session_policy.py`, `official_data_v22.py`

`AUTH_KEY` 헤더로 KRX, `crtfc_key` 요청인자로 OpenDART를 호출합니다. 인증 문자열은 로그·파일에 기록하지 않습니다. KRX 엔드포인트는 기존 v2.2 코드가 사용 중인 `https://data-dbg.krx.co.kr/svc/apis/sto/...`이며 **실제 승인 계정으로 호출하기 전에는 접근 여부를 보증할 수 없습니다**.

## 코드 구성

| 파일 | 기능 |
|---|---|
| `official_probe/clients.py` | KRX 주식 일별 데이터, OpenDART 기업코드·공시·제한된 재무 샘플 API 연결 |
| `official_probe/validation.py` | OHLC 검증, 기준일·중복코드·누락·누적 거래량 구분, 공시 접수일 필터 |
| `official_probe/__main__.py` | 기본 모의실행, 라이브 선택, 10종목 상한, 결과 작성(비공개) |
| `tests/test_official_probe.py` | 네트워크 없는 회귀 테스트 24개 |
| `.github/workflows/official-probe.yml` | 수동 workflow_dispatch만 지원; push·schedule 트리거 없음 |
| `.gitignore` | 로컬 연결 결과 무단 commit 방지 |

## 로컬 / WORK 적용 시

```bash
# 기존 브랜치 최신 상태 및 README 확인 후
# 반드시 아래 검사부터 시작

git apply --check official_api_preparation.patch
git apply official_api_preparation.patch

python -m unittest discover -s tests -p 'test_official_probe.py' -v
# dry-run; docs/data.json 기준일과 같거나 더 이른 날짜 지정
python -m official_probe --asof 2026-10-16
```

현재 GitHub 기준일이 바뀌면 `--asof`를 새로운 **완료된 거래일**로 변경합니다. `--symbols 005930,...`로 선택하려면 `docs/data.json`에 해당 종목이 존재해야 하며 시장구분을 임의 추정하지 않습니다. 기본값은 그 파일에서 앞의 10종목입니다.

승인·GitHub Secrets 설정 후 수동 실행 예시(키를 출력하거나 코드에 기록하지 않음):

```
GitHub → Actions → Official KRX and OpenDART API Probe (manual only)
→ Run workflow
→ mode: live
→ provider: both
→ asof: 해당 파일 기준일 이하의 완료 거래일
→ symbols: (생략 시 10개)
```

필요한 GitHub Actions Secrets 이름: `KRX_API_KEY`, `DART_API_KEY`. 기존 프로젝트와 같은 이름입니다. 인증키 자체를 CHAT/WORK에 입력하거나 첨부하지 마세요.

## 안전 제약 / 다음 개발

1. 승인되지 않은 KRX API 서비스는 `key_required`/`failed`/`requested_symbols_not_found`로 기록하고 임의 소스를 대체하지 않습니다.
2. 모의실행·연결 성공을 **확정 데이터 검증**이라고 보고하지 않습니다.
3. `docs/` 결과, 기존 Excel, 날짜별 보고서, 외국인·기관 매집 점수를 변경하지 않습니다.
4. 금융 재무정보는 공시 당시 공개 시점의 정합성/정정판 이력 확인 이전에 점수 산식에 넣지 않습니다.
5. 이 패치로 전 종목 수급이 확보되지는 않습니다. 정규장 전용 거래량과 투자자별 수급의 공식 데이터 계약이 먼저 필요합니다.
6. GitHub Actions 실행 시 비용·요청한도·저작권/재배포 허용 조건을 검토해야 합니다. WORK에서 선제 반복실행 금지.
