# v3 사전개발: KRX 두 시장 로컬 비공개 SQLite 아카이브 (오프라인)

## 목적

기존 `v3_evidence_chain.py`는 일자별 원시 입력 지문/해시체인의 **검증 규칙**만 제공한다. 새 `v3_local_private_store.py`는 이 계약을 재사용해 **실제 사용자가 통제하는 로컬 PC 디스크에서** KOSPI·KOSDAQ 두 시장의 원본 바이트를 하나의 SQLite 트랜잭션으로 저장하고 복원할 수 있도록 설계한 *독립 오프라인 프로토타입*이다.

**현재 GitHub Actions 수집 워크플로와 연결하지 않았다.** GitHub-hosted runner는 여전히 휘발성이고, KRX 서비스가 승인되거나 원천 저장·재배포 권한이 확인된 상태가 아니다. 코드를 실행하지 않으면 데이터 수집/파일 생성도 없다.

## 구현된 기능

1. 원본 데이터는 반드시 **공개 Git 저장소 디렉터리 외부**의 사용자 지정 SQLite 파일로만 기록한다. 기본 경로는 `~/.local/share/korean-stock-screener/v3/private_market.sqlite3`.
2. POSIX 시스템에서는 개인 디렉터리 접근권한 0700, SQLite 파일 0600을 요구한다. 그룹·타인에게 열린 기존 저장소는 거절한다. Windows는 별도 ACL 구성과 디스크 암호화가 필요하다.
3. KOSPI와 KOSDAQ 원시 바이트 두 개를 **동일 날짜 1개 행**에 함께 기록한다. 한 시장 누락·비어 있는 파일·동일 원시 응답 재사용은 거절한다. 트랜잭션 전체가 실패하면 신규 일별 데이터 0개를 보장한다.
4. 원본 바이트 SHA256/파일크기/각 시장 UTC 수신 시각을 계산해 `v3_evidence_chain`의 연속 날짜 해시체인에 연결한다. 보관된 원본을 검사할 때 실제 SQLite BLOB에서 SHA256을 다시 계산한다.
5. 동일 날짜와 동일 수신 내용의 재입력은 **멱등 처리**하고, 바뀐 원본·날짜 역순 추가는 실패하도록 하며 이전 행을 변경하지 않는다.
6. SQLite는 `BEGIN IMMEDIATE` 트랜잭션, FULL 동기화, DELETE 저널 모드를 사용한다. `inspect_private_store`는 **SQLite 읽기 전용 연결**을 사용해 전체 원본 지문/체인을 확인하고 수량과 상태만 반환한다.
7. 공개 화면·Excel·깃 커밋·로그에 원본 JSON/바이트, API 키, 기업별 수급 데이터나 가격을 전송하는 기능은 없다.

## 사용 예시 — 합성 입력만 사용

```python
from v3_local_private_store import store_pair, inspect_private_store

report = store_pair(
    database_path="/private/krx/snapshots.sqlite3",
    asof="2026-10-16",
    kospi_raw=b"synthetic-kospi-record",
    kosdaq_raw=b"synthetic-kosdaq-record",
    kospi_received_at_utc="2026-10-16T07:10:00Z",
    kosdaq_received_at_utc="2026-10-16T07:10:00Z",
)
assert report["authorized_for_v3_score"] is False
audit = inspect_private_store(database_path="/private/krx/snapshots.sqlite3")
```

예제의 `/private/krx/`는 **사용자가 직접 준비한 접근 제한 경로**를 설명하기 위한 가상 경로다. 로컬 권한이 없는 환경에서 그대로 실행해 성공한다는 뜻이 아니다.

## 명시적 제한

- **저장만 가능**, 현재 다운로드·자동 수집·비공개 클라우드 백업·원본 암호화는 미구현이다. 사용자 로컬 컴퓨터 분실/악성코드·DB 삭제·디스크 오류에 대비해 OS 전체 디스크 암호화 및 별도 백업이 필요하다.
- SHA256 체인은 **독립적으로 고정된 외부 헤드가 없으므로 전체 체인 재작성까지 방지하지 못한다**. 0600 권한도 완전한 보안 보장은 아니다.
- 사용자가 주장한 수신 시각의 신뢰성, 공급원 진위, 데이터 저장/재배포 약관, KRX 정규장 전용 거래량, 역사적 수정주가·상장폐지·재무 PIT는 이 모듈에서 인증하지 않는다.
- 실제 **원본 보관이 계약상 허용되는지 확인**한 뒤에만 공급자 자료를 저장해야 한다. 코스피·코스닥 원본을 GitHub Actions 아티팩트나 공개 사이트에 게시하지 않는다.
- `v3_release_ready`와 `confirmed_buy`는 여전히 활성화되지 않는다. 이 저장소의 데이터를 직접 종목 점수에 연결하는 코드는 추가하지 않았다.

테스트 (실제 시장 데이터 없이 임시 디렉터리에 합성 바이트만 사용):

```bash
python -m unittest discover -s tests -p 'test_v3_local_private_store.py' -v
python -m py_compile v3_local_private_store.py
```
