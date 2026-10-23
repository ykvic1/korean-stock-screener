"""Manual Chromium smoke test of unified public research workspace at 390x844."""
from pathlib import Path
from zipfile import ZipFile
import json
import os
from playwright.sync_api import sync_playwright

# Production by default; CI also tests real checked-in market snapshots
# through a local static server, avoiding any external API calls.
URL = os.environ.get("V3_UNIFIED_URL", "https://ykvic1.github.io/korean-stock-screener/")
OUT = Path("verification")
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=2, is_mobile=True, has_touch=True, accept_downloads=True
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    assert page.url.rstrip("/") == URL.rstrip("/"), "root should serve unified UI directly"
    # The source snapshot overview is deliberately collapsed until requested.
    assert not page.locator("#marketSummary").evaluate("(el) => el.open")
    assert not page.locator("#advancedFilters").evaluate("(el) => el.open")
    assert page.locator("#stockSearch").is_visible()
    page.get_by_role("button", name="전 종목", exact=True).click()
    page.locator(".u-stock").first.wait_for(timeout=45000)
    assert page.locator("#sort").input_value() == "score", "default sort must follow visible scores"
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "false"
    # Only source-dated provisional V3 points are displayed; no V2.2 rank.
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "false"
    assert page.locator(".u-stock").first.locator(".u-risk-first").is_visible()
    assert page.locator(".u-stock").first.locator(".u-research-summary").is_visible()
    page.locator(".u-stock").first.locator(".u-factor-details summary").click()
    assert page.locator(".u-factor-details").first.get_by_text("6개 영역별 점수").is_visible()
    assert page.locator(".u-scoresix").first.get_by_text("거래량·OBV /20").is_visible()
    volume = context.request.get(URL + "v3-volume-obv-observed.json").json()
    assert volume["schema"] == "v3-current-market-volume-obv-provisional-research-0.1"
    assert volume["observed_volume_candidate_issuers"] > 0
    assert volume["asof"] == context.request.get(URL + "data.json").json()["date"]
    # The main scorecard must actually SHOW the sourced 16/20 partial score.
    # A renamed but empty IAS card is an erroneous rollout.
    shown = page.locator('.u-scoresix span[data-pillar="volume"] b').first.inner_text()
    assert shown.strip() != "—", "real observed volume candidate not applied"
    assert page.locator(".u-scoresix").first.get_by_text("기술적 분석 /25").is_visible()
    assert page.locator(".u-scoresix").first.locator(".u-factor-unverified").count() == 6
    data = context.request.get(URL + "data.json").json()
    assert data["version"] == "2.0" and data["status"] == "success"
    page.locator("#marketSummary summary").click()
    assert page.locator("#statAnalyzed").is_visible()
    displayed = int(page.locator("#statAnalyzed").inner_text().replace(",", ""))
    assert 0 < displayed <= data["candidate_count"]
    assert page.get_by_text("V3 잠정점수 산출 종목").is_visible()
    assert page.locator("#statV3").inner_text().strip().endswith("종목")
    page.locator("#marketSummary summary").click()
    assert not page.locator("#marketSummary").evaluate("(el) => el.open")
    # The V3 landing page must expose real saved-source progress without
    # claiming that current research coverage equals a verified investment rank.
    assert not page.locator("#dataReadiness").evaluate("(el) => el.open")
    page.locator("#dataReadiness summary").click()
    page.wait_for_function(
        "() => document.getElementById('readinessStatus').textContent.includes('자료 기준일')"
        " || document.getElementById('readinessStatus').textContent.includes('출처 재검증 중')",
        timeout=15000
    )
    readiness = page.locator("#readinessStatus").inner_text()
    if "자료 기준일" in readiness:
        observed = context.request.get(URL + "v3-current-observed-all-listed-summary.json").json()
        historical = context.request.get(URL + "v3-real-source-six-area-coverage.json").json()
        joined = context.request.get(URL + "v3-all-listed-scorecard-summary.json").json()
        expected_financial = sum(historical["historical_dart_company_years_with_accounts_by_fiscal"].values())
        expected_attempted = sum(historical["historical_dart_company_years_attempted_by_fiscal"].values())
        assert page.locator("#readinessFinancial").inner_text() == (
            f"{expected_financial:,} / {expected_attempted:,}"
        )
        assert page.locator("#readinessPrice").inner_text() == (
            f"{observed['observed_issuer_counts']['technical']:,} / {observed['listed_total']:,}"
        )
        if joined["asof"] == observed["asof"] and joined["listed_total"] == observed["listed_total"]:
            assert page.locator("#readinessCashflow").inner_text() == (
                f"{joined['observed_source_depth']['three_year_receipt_matched_fcf_issuers']:,} / "
                f"{observed['listed_total']:,}"
            )
        else:
            assert page.locator("#readinessCashflow").inner_text() == "미확인"
            assert "3개년 FCF 갱신 대기" in page.locator("#readinessNotes").inner_text()
        assert page.locator("#readinessCertified").inner_text() == "0종목"
    else:
        # Market-close transition may leave legitimately mismatched snapshot
        # dates; the UI must show unknown rather than silently merge dates.
        for field in ("Financial", "Price", "Cashflow", "Certified"):
            assert page.locator("#readiness" + field).inner_text() == "미확인"
    assert page.get_by_text("공식 수집 실행 기록 확인 ↗").is_visible()
    page.locator("#dataReadiness summary").click()
    assert not page.locator("#dataReadiness").evaluate("(el) => el.open")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), "unified mobile horizontal overflow"
    code = page.locator(".u-stock").first.locator(".u-stock-name small").inner_text().split(" · ")[0]
    search = page.locator("#stockSearch")
    search.fill(code)
    assert page.locator(".u-stock").count() == 1
    # Search must not be trapped inside TOP100 nor whichever RSI/quant
    # secondary tab was selected; never silently omit a lower-ranked ticker.
    page.locator('[data-tab="rsi"]').click()
    other = data["stocks"][-1]["code"]
    assert other != code
    search.fill(other)
    assert page.locator(".u-stock").count() == 1
    assert other in page.locator(".u-stock").first.inner_text()
    assert "전체 시장 검색" in page.locator("#resultCount").inner_text()
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "false"
    page.get_by_role("button", name="전 종목", exact=True).click()
    search.fill("__unknown_stock__")
    assert page.locator(".u-stock").count() == 0
    assert page.get_by_text("해당 조건을 만족하는 종목이 없습니다.", exact=False).is_visible()
    search.fill("")
    page.locator("#top100Only").click()
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "true"
    # Zero source-scored candidates is allowed, and is NOT filled with V2.2.
    page.locator("#top100Only").click()
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "false"
    assert page.locator(".u-stock").count() > 0
    page.locator("#advancedFilters summary").click()
    assert page.locator("#sort").is_visible()
    page.locator("#sort").select_option("score")
    page.locator("#advancedFilters summary").click()
    # Card order is provisional evidence-first, not the old 2.2 ranking.
    assert page.locator(".u-stock-main .u-risk-first").first.is_visible()
    assert "배점 확보" in page.locator(".u-research-summary").first.inner_text()
    page.locator(".u-stock").first.click()
    assert page.locator("#detail").is_visible()
    details = page.locator("#detail").inner_text()
    assert "일봉 · 스윙 관찰" in details and "주봉 · 중기/장기 관찰" in details
    assert "V3 통합 100점" in details and "미검증" in details
    assert "손실 위험" in details
    page.locator("#detailClose").click()
    for scope in ("patterns","rsi","quant","discover"):
        page.locator(f'[data-tab="{scope}"]').click()
        assert page.locator(f'[data-tab="{scope}"]').get_attribute("aria-pressed") == "true"
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), f"horizontal overflow in {scope}"
    # Existing exporter remains downloadable without moving to legacy UI.
    with page.expect_download(timeout=30000) as dl:
        page.get_by_role("link", name="전체 엑셀 ↓").click()
    downloaded = dl.value
    downloaded.save_as(str(OUT / "unified-mobile-export.xlsx"))
    with ZipFile(OUT / "unified-mobile-export.xlsx") as z:
        assert z.testzip() is None and "xl/workbook.xml" in z.namelist()
    page.screenshot(path=str(OUT / "unified-mobile.png"), full_page=True)
    assert not errors, errors
    (OUT / "unified-mobile-result.json").write_text(
        json.dumps({
            "ui": "unified V3 primary workspace", "page": page.url,
            "viewport": "390x844", "analysed_stocks": displayed,
            "certified_v3_investable_score": False,
            "checks": ["primary page redirect", "one-screen search",
                       "daily+weekly research", "source status", "five scopes",
                       "V3 provisional coverage sum", "downside first",
                       "same-screen details", "Excel export", "horizontal overflow",
                       "no JavaScript errors"]
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    browser.close()
