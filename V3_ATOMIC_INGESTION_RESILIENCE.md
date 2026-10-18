# v3 과제 5 — 공식 API 승인 전 이중시장 수집 장애·페이지 누락 복구

새 v3_atomic_ingestion.py의 collect_atomic_pair()는 **외부에서 주입한 읽기전용 fetch_page 함수**만 호출하는 오프라인 모듈이다. 외부 자격증명·URL·API 요청은 구현하지 않았다.

* KOSPI/KOSDAQ **양 시장 전체** 자료를 하나의 메모리 스테이징 단계로 수집한다. 한 시장이라도 수집 실패하면 정상 일부 데이터도 반환하지 않으므로 완전한 스냅샷으로 가장하지 않는다.
* 동일 asof, issuer_id, 종목코드·시장·시가/거래량 타입 및 페이지 순서·종료 커서를 검사한다.
* 동시점에 유효한 과거 **예상 전체 종목 목록**을 반드시 입력받으며, 회신된 각 시장의 종목 집합이 정확히 같아야 한다. 실제 KRX 과거 상장·상폐 목록임을 독립 인증한 상태는 아니다.
* 명시적 일시 장애 예외에 한해서만 페이지당 최대 3회 재시도한다. 인증/파싱/데이터 불일치는 자동 복구하지 않고 즉시 실패. 재시도 예산·페이지 제한·반복 커서로 무한수집 차단.
* 어제 받은 가격이나 마지막 정상 스냅샷으로 빈칸을 채우지 않는다. 성공할 때만 KOSPI/KOSDAQ 각각의 원본 정렬 JSON **개인용 메모리 바이트**와 SHA256 검산값을 제공한다.
* 실제 조회 공급자별 응답 포맷 매핑·레이트리밋/지수백오프·중단 재개 등은 공식 API 승인 이후 실제 계약에 맞춰 구현해야 한다.
* 기존 개인용 SQLite 백업/원본 저장소에 자동 업로드하거나 공개 Pages·Excel로 배포하지 않는다.
* 모델이 원본 자체를 생성·보충하지 않는다. official_provider_data_verified=false, safe_for_public_release=false, confirmed_buy=false.

테스트: python -m unittest discover -s tests -p 'test_v3_atomic_ingestion.py' -v
