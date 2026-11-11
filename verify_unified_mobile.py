"""Manual Chromium smoke test of unified public research workspace at 390x844."""
from pathlib import Path
from zipfile import ZipFile
from io import StringIO
import csv
import json
import os
import re
from datetime import date
from playwright.sync_api import sync_playwright

# Production by default; CI also tests real checked-in market snapshots
# through a local static server, avoiding any external API calls.
URL = os.environ.get("V3_UNIFIED_URL", "https://ykvic1.github.io/korean-stock-screener/")
OUT = Path("verification")
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    # CI uses verified preinstalled Chrome on the GitHub Ubuntu image;
    # manual local runs may still use Playwright's bundled Chromium.
    browser = p.chromium.launch(
        channel=os.environ.get('V3_MOBILE_BROWSER_CHANNEL') or 'chromium',
        headless=True,
    )
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        device_scale_factor=2, is_mobile=True, has_touch=True, accept_downloads=True
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "[V3 workspace load]" in m.text else None)
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    assert page.url.rstrip("/") == URL.rstrip("/"), "root should serve unified UI directly"
    # Current PR259 interface has no coverageGateStatus/Rows elements.
    # Check the real market source snapshot and the unchanged stock-card UI.
    observed = context.request.get(URL + "v3-current-observed-all-listed-summary.json").json()
    evidence = context.request.get(URL + "v3-real-source-six-area-coverage.json").json()
    independent = context.request.get(URL + "v3-all-listed-scorecard-summary.json").json()
    # The six-area aggregate can lag because weekly technical evidence rotates
    # separately. Never use yesterday's DART census for today's annual totals;
    # a same-day, independently replayed scorecard may nonetheless contain
    # actual new DART receipts with valid provisional financial/value scores.
    market_day = date.fromisoformat(observed["asof"])
    dart_day = date.fromisoformat(evidence["market_asof"])
    dart_lag = (market_day - dart_day).days
    assert 0 <= dart_lag <= 10, "historical DART source must be a bounded prior market day"
    if dart_lag == 0:
        assert observed["listed_total"] == evidence["listed_total"], (
            "same-day DART issuer census mismatch must fail closed"
        )
    else:
        assert market_day.year == dart_day.year
    # The independently replayed legacy aggregate may also lag during the
    # new close's source publication. Do not certify it as today's data.
    independent_day = date.fromisoformat(independent["asof"])
    independent_lag = (market_day - independent_day).days
    assert 0 <= independent_lag <= 10, "independent replay must not be future or unbounded stale"
    if independent_lag == 0:
        assert independent["listed_total"] == observed["listed_total"]
        # A matching market date alone does not establish a matching source.
        # The independent price replay may correctly reject a same-day market
        # snapshot after its digest changed. In that case, keep technical and
        # momentum as different, uncomparable observations (not zero scores).
        # Volume can still be independently checked if its digest agrees.
        assert independent["source_sha256"]["catalog"] == observed["source_sha256"]["catalog"]
        assert independent["source_sha256"]["volume_observed"] == observed["source_sha256"]["volume"]
        assert independent["provisional_numeric_issuer_counts"]["volume"] == (
            observed["observed_issuer_counts"]["volume"]
        ), "shared source volume count mismatch"
        price_digest = independent["source_sha256"]["same_day_price_replay"]
        if price_digest is not None:
            assert price_digest == observed["source_sha256"]["price"], (
                "independent replay cannot treat a different price snapshot as current"
            )
            for area in ("technical", "momentum"):
                assert independent["provisional_numeric_issuer_counts"][area] == (
                    observed["observed_issuer_counts"][area]
                ), f"shared source {area} count mismatch"
        for area in ("financial", "valuation"):
            for snapshot in (independent["provisional_numeric_issuer_counts"],
                             observed["observed_issuer_counts"]):
                assert type(snapshot[area]) is int and 0 <= snapshot[area] <= independent["listed_total"]
    # Neither source can be promoted to independently certified investable scores.
    assert independent["full_six_pillar_certified_count"] == 0
    assert observed["certified_100point_count"] == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page overflows mobile"
    # The source snapshot overview is deliberately collapsed until requested.
    assert not page.locator("#marketSummary").evaluate("(el) => el.open")
    assert not page.locator("#advancedFilters").evaluate("(el) => el.open")
    assert page.locator("#stockSearch").is_visible()
    assert page.locator("#liveUpdates").is_visible()
    assert page.locator(".u-header-more > summary").is_visible()
    page.locator(".u-header-more > summary").click()
    assert page.locator('.u-header-actions a[href="patch-notes.html"]').is_visible()
    page.locator(".u-header-more > summary").click()
    # Research audit and two-stock comparison are available on demand.
    assert not page.locator("#researchPanel").evaluate("(el) => el.open")
    assert not page.locator("#stockCompare").evaluate("(el) => el.open")
    page.wait_for_function(
        "() => document.querySelector('#liveProgressCoverage')?.textContent.includes('종목')",
        timeout=20000,
    )
    public_receipts = context.request.get(
        URL + "v3-dart-historical-receipt-progress.json"
    ).json()
    comparison = context.request.get(
        URL + "v3-operational-research-comparison.json"
    ).json()
    r1d = comparison.get("operational_coverage")
    if (comparison.get("version") in ("v3-stable-r1d-operational-research", "v3-stable-r1e-operational-research")
            and r1d and comparison["asof"] <= observed["asof"]
            and r1d["listed_total"] == observed["listed_total"]):
        assert r1d["budgets"] == {"volume": 25, "technical": 30,
                                    "financial": 12.5, "valuation": 12.5,
                                    "momentum": 20}
        assert r1d["investment_score_certified"] is False
        model_label = "R1e" if "r1e" in comparison["version"] else "R1d"
        assert page.locator("#liveProgressAsOf").inner_text() == (
            comparison["asof"] + " · " + model_label + " 점수"
        )
        assert page.locator("#liveProgressCoverage").inner_text() == (
            f'{r1d["available_100_count"]:,} / {r1d["listed_total"]:,}종목 ({model_label})'
        )
    else:
        assert page.locator("#liveProgressAsOf").inner_text() == observed["asof"]
        assert page.locator("#liveProgressCoverage").inner_text() == (
            f'{observed["provisional_complete_100point_observation_count"]:,} / '
            f'{observed["listed_total"]:,}종목 (기존 모델)'
        )
    assert page.locator("#liveProgressDart").inner_text() == (
        f'{public_receipts["filing_months_checkpointed"]:,} / 120개월'
    )
    page.get_by_role("button", name="전 종목", exact=True).click()
    try:
        page.locator(".u-stock").first.wait_for(timeout=45000)
    except Exception:
        print("Unified UI diagnostics (no private raw market data):", json.dumps({
            "page_errors": errors[-5:],
            "state": page.locator("#state").inner_text(timeout=5000),
            "result_count": page.locator("#resultCount").inner_text(timeout=5000),
            "stock_cards": page.locator(".u-stock").count(),
            "empty_state": page.locator("#stockList").inner_text(timeout=5000)[:200],
            "sort": page.locator("#sort").input_value(timeout=5000),
        }, ensure_ascii=False))
        raise
    assert page.locator("#resultCount").inner_text().startswith(
        f'{observed["listed_total"]:,}종목'
    ), "R1d interactive all-listed view must include unpriced issuers"
    # Before selection, use the whole desktop width for a two-column issuer list.
    page.set_viewport_size({"width": 1440, "height": 900})
    desktop_grid = page.evaluate("""() => {
        const cards = [...document.querySelectorAll('#stockList .u-stock')].slice(0, 2);
        if (cards.length < 2) return {count: cards.length};
        const a = cards[0].getBoundingClientRect();
        const b = cards[1].getBoundingClientRect();
        return {
            count: cards.length,
            detailHidden: document.querySelector('#detail').hidden,
            firstTop: Math.round(a.top), secondTop: Math.round(b.top),
            firstLeft: Math.round(a.left), secondLeft: Math.round(b.left),
            firstWidth: Math.round(a.width),
            overflow: document.documentElement.scrollWidth > innerWidth,
        };
    }""")
    assert desktop_grid["count"] == 2 and desktop_grid["detailHidden"], desktop_grid
    assert abs(desktop_grid["firstTop"] - desktop_grid["secondTop"]) < 3, desktop_grid
    assert desktop_grid["secondLeft"] > desktop_grid["firstLeft"] + 300, desktop_grid
    assert desktop_grid["firstWidth"] > 450 and not desktop_grid["overflow"], desktop_grid
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator("#sort").input_value() == "ops", "default sort must use provisional R1 operational points"
    operational = context.request.get(URL + "v3-operational-research-scores.json").json()
    first_card = page.locator(".u-stock").first
    first_code = first_card.get_attribute("data-stock")
    first_score = re.search(r"[0-9]+(?:\.[0-9]+)?", first_card.locator(".u-card-score strong").inner_text())
    assert first_score and float(first_score.group()) == operational["rows"][first_code]["score"], "visible operational score must equal server/CSV model"
    first_card.click()
    assert page.locator(".u-operational-detail").is_visible()
    if operational["rows"][first_code]["available"] < 100:
        assert page.locator(".u-operational-missing summary").count() == 1
        assert "미관측 항목" in page.locator(".u-operational-missing summary").inner_text()
    else:
        assert "평가항목 100점 배점의 근거가 모두 관측" in page.locator(".u-operational-detail").inner_text()
    page.locator(".u-operational-detail details summary").filter(has_text="신규 점수 세부 근거").click()
    assert "거래 구조 12점은 완성된 장기 거래대금" in page.locator(".u-operational-detail").inner_text()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "operational detail must fit mobile viewport"
    page.locator("#detailClose").click()
    # The existing comparison paths remain available and independently tested.
    page.locator("#advancedFilters > summary").click()
    page.locator("#sort").select_option("stable")
    page.locator("#advancedFilters > summary").click()
    assert page.locator("#sort").input_value() == "stable", "legacy stability comparison remains available"
    assert "안정형 보정" in page.locator(".u-card-score").first.inner_text()
    assert "시총" in page.locator(".u-card-meta").first.inner_text()
    assert "당일 거래대금" in page.locator(".u-card-meta").first.inner_text()
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "false"
    # The actual stock list must show original factor dates, including carried
    # observations, rather than displaying an undated score-only ranking.
    card_dates = page.locator(".u-stock .u-card-meta")
    assert card_dates.count() > 0, "issuer score as-of dates must render on cards"
    assert re.search(r"\d{4}-\d{2}-\d{2}", card_dates.first.inner_text())
    dated_factors = page.locator(".u-stock .u-compact-pillars span[title]")
    assert dated_factors.count() >= 5
    assert "기준일" in dated_factors.first.get_attribute("title")
    assert "미검증" in dated_factors.first.get_attribute("title")
    # The requested research quick filter expands to all stocks, not just TOP100.
    assert page.locator("#obvLagOnly").is_visible()
    page.locator("#obvLagOnly").click()
    assert page.locator("#obvLagOnly").get_attribute("aria-pressed") == "true"
    assert page.locator('[data-tab="all"]').get_attribute("aria-pressed") == "true"
    assert "OBV 상승·주가 미반영" in page.locator("#resultCount").inner_text()
    assert "매수 신호는 아닙니다" in page.locator("#filterNotice").inner_text()
    page.locator("#obvLagOnly").click()
    assert page.locator("#obvLagOnly").get_attribute("aria-pressed") == "false"
    # Personal beta point scale is a provisional observed-budget normalization, not certification.
    assert page.locator("#personalRank").is_visible()
    page.locator("#personalRank").click()
    assert page.locator("#personalRank").get_attribute("aria-pressed") == "true"
    assert page.locator("#sort").input_value() == "personal"
    assert page.locator('[data-tab="all"]').get_attribute("aria-pressed") == "true"
    assert page.locator(".u-stock .u-card-score").first.is_visible()
    # Carried prior observations legitimately remain visible for a stock
    # without a same-day quote and may occupy the first full-market page.
    # Verify that restricted panel does not pretend to have a current chart.
    first_personal_card = page.locator(".u-stock").first
    if "종가 미관측" in first_personal_card.locator(".u-card-price small").inner_text():
        first_personal_card.click()
        assert "현재 가격 관측 목록에서 제외된 종목" in page.locator("#detail").inner_text()
        assert "가격·손절선·차트·기존 점수를 임의로 채우지 않습니다" in page.locator("#detail").inner_text()
        page.locator("#detailClose").click()
    # For price/stop visual contract, search a known current-priced issuer.
    # Search is global and independent of the visible top-20 sort window.
    priced_code = context.request.get(URL + "data.json").json()["stocks"][0]["code"]
    page.locator("#stockSearch").fill(priced_code)
    personal_card = page.locator(".u-stock").filter(
        has=page.locator(".u-card-price > small").filter(has_text="종가 ·")
    ).first
    assert personal_card.count() == 1, "current-priced stock must remain searchable"
    personal_label = personal_card.locator(".u-card-score > small").last.inner_text()
    assert "개인환산" in personal_label
    first_normalized = personal_label.split("개인환산 ", 1)[1].removesuffix("점")
    # Complete 100/100 observations and eligible *partial* personal ratios
    # are distinct: the latter needs all five pillars and at least 75 points
    # of observed budget, even when the issuer census has 0 complete rows.
    personal_card.click()
    assert "개인 연구용 100점 환산 · 미인증" in page.locator("#detail").inner_text()
    # Sketch-inspired detail scorecard: visible five bars plus a vertical 100-point
    # total. Show earned, observed-but-unearned, and missing budgets separately.
    scorecard = page.locator("#detail .u-score-visual")
    assert scorecard.is_visible()
    assert "총 100점 배점 · 미인증" in scorecard.inner_text()
    assert scorecard.locator(".u-score-total-track").is_visible()
    assert scorecard.locator(".u-score-row").count() == 5
    statuses = scorecard.locator(".u-score-row-status").all_inner_texts()
    available_by_pillar = [
        int(match.group(1)) if (match := re.search(r"확보 (\d+)/", status)) else 0
        for status in statuses
    ]
    ratio_eligible = all(points > 0 for points in available_by_pillar) and (
        sum(available_by_pillar) >= 75
    )
    assert (first_normalized != "—") == ratio_eligible, (
        "personal ratio must follow per-issuer five-pillar/75-point coverage,"
        " not the market-wide 100/100 issuer count"
    )
    if ratio_eligible:
        assert 0 <= float(first_normalized) <= 100
    assert scorecard.locator(".u-score-row").evaluate_all(
        "(rows) => rows.map(row => row.dataset.pillar)"
    ) == ["momentum", "valuation", "financial", "technical", "volume"]
    assert scorecard.locator(".u-score-row .u-score-track").count() == 5
    assert scorecard.locator(".u-score-row .u-score-earned").evaluate_all(
        "(nodes) => nodes.some(node => node.getBoundingClientRect().width > 5)"
    ), "CSP-safe CSSOM must actually paint nonzero horizontal score bars"
    assert scorecard.locator(".u-score-total-track > .u-score-earned").evaluate(
        "(node) => node.getBoundingClientRect().height > 5"
    ), "CSP-safe CSSOM must actually paint the vertical score bar"
    assert "미확보는 0점이 아닙니다" in scorecard.inner_text()
    # Each area opens directly from the bar. Source-limited evidence must
    # remain research-only, and expanded content must fit the narrow viewport.
    assert scorecard.locator(".u-score-row summary").count() == 5
    for pillar_index, (pillar, evidence_text) in enumerate([
        ("momentum", "시장 대비 20일"),
        ("technical", "일봉 패턴"),
        ("volume", "거래량 급증 후 매물 소화"),
        ("financial", "FCF"),
        ("valuation", "FCF"),
    ]):
        expander = scorecard.locator(f'.u-score-row[data-pillar="{pillar}"]')
        assert not expander.evaluate("(el) => el.open")
        expander.locator("summary").click()
        assert expander.evaluate("(el) => el.open")
        if expander.locator(".u-last-factor").count():
            retained_text = expander.locator(".u-last-factor").inner_text()
            assert "관측 점수 유지" in retained_text
            assert "오늘 새로 계산된 점수가 아니며" in retained_text
            assert re.search(r"\d{4}-\d{2}-\d{2}", retained_text)
        elif available_by_pillar[pillar_index] > 0:
            assert evidence_text in expander.inner_text(), pillar
        assert "미확보는 0점으로 계산한 것이 아닙니다" in expander.inner_text()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), f"{pillar} evidence expansion must not overflow 390px mobile"
        expander.locator("summary").click()
        assert not expander.evaluate("(el) => el.open")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), "five-area stock detail scorecard must not overflow mobile viewport"
    # A stop reference is displayed once, unscored, using the current 20/60
    # structural receipt. Never revive the old 20/45 scored-stop expander.
    stop_heading=page.locator("#detail h3").filter(has_text="20·60일 구조적 손절 참고선")
    assert stop_heading.count()==1
    assert "기존 점수용 손절선" not in page.locator("#detail").inner_text()
    assert page.locator("#detail details.u-stop-evidence").count()==0
    stop_reference=page.locator("#detail section.u-stop-reference")
    if stop_reference.count():
        assert stop_reference.locator(".u-stop-evidence > div").count()==5
        assert "어떤 가점·감점도 없습니다" in stop_reference.inner_text()
        assert "하단 매물대" in stop_reference.inner_text()
        assert "패턴 무효화" in stop_reference.inner_text()
    else:
        assert "구조적 참고선 관측 대기" in page.locator("#detail").inner_text()
    for width in (320,390,430):
        page.set_viewport_size({"width":width,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
            "structural stop reference must fit mobile",width)
    page.set_viewport_size({"width":390,"height":844})
    # Every selected stock must disclose its FCF state without guessing missing accounts.
    deeper = page.locator("#detail .u-detail-evidence")
    assert not deeper.evaluate("(el) => el.open")
    deeper.locator("summary").first.click()
    fcf_status = page.locator("#detail .u-fcf-stock-status")
    assert fcf_status.is_visible()
    assert "FCF 확보 상태" in fcf_status.locator("summary").inner_text()
    fcf_status.locator("summary").click()
    fcf_detail = fcf_status.inner_text()
    if "연도별 공식 현금흐름 계정 확보 상태" in fcf_detail:
        assert all(f"{year}년" in fcf_detail for year in (2023, 2024, 2025))
        assert "기업이 실제로 공시하지 않았다고 단정할 수 없습니다" in fcf_detail
    else:
        # The tiny issuer-year derivative can lag the cashflow collection;
        # do not invent an account-specific reason during that interval.
        assert "개별 공시 원장" in fcf_detail
    assert "0점" in fcf_detail
    page.locator("#personalRank").click()
    page.locator("#stockSearch").fill("")
    assert page.locator("#sort").input_value() == "stable"
    assert page.locator("#personalRank").get_attribute("aria-pressed") == "false"
    # Compare only stocks with the same available ceilings for ALL five pillars.
    page.locator("#advancedFilters > summary").click()
    assert page.locator("#coverageGroup option").count() > 1
    group = page.locator("#coverageGroup option").nth(1).get_attribute("value")
    assert group is not None and group.count("-") == 4
    page.locator("#coverageGroup").select_option(group)
    assert "5영역별 확보배점" in page.locator("#resultCount").inner_text()
    assert "세부원천·PIT" in page.locator("#filterNotice").inner_text()
    assert page.locator(".u-stock").count() > 0
    page.locator("#coverageGroup").select_option("all")
    assert "동일 그룹" not in page.locator("#resultCount").inner_text()
    page.locator("#advancedFilters > summary").click()
    # Side-by-side real tickers: compare earned/available and all five pillar evidence.
    page.locator("#stockCompare > summary").click()
    assert page.locator("#compareForm").is_visible()
    page.locator("#compareCodeA").fill("005930")
    page.locator("#compareCodeB").fill("000660")
    page.locator("#compareRun").click()
    assert page.locator("#compareResults").is_visible()
    assert "삼성전자" in page.locator("#compareResults").inner_text()
    assert "SK하이닉스" in page.locator("#compareResults").inner_text()
    assert page.locator("#compareResults .u-compare-table tbody tr").count() == 5
    assert page.locator("#compareResults .u-compare-bars-row").count() == 5
    assert page.locator("#compareResults .u-compare-meter").count() == 10
    assert "미확보" in page.locator("#compareResults .u-compare-bars").inner_text()
    # CSP-safe CSSOM widths: each bar represents the full pillar ceiling.
    assert page.locator("#compareResults .u-compare-meter").evaluate_all("""els =>
        els.every(el => Math.abs(
            [...el.children].reduce((sum,part) =>
                sum + (parseFloat(part.style.width) || 0), 0) - 100
        ) < 0.04)
    """), "comparison bars must be painted, not left at zero by CSP"
    assert page.evaluate("""() => {
        const box=document.querySelector('#compareResults .u-compare-bars').getBoundingClientRect();
        return box.left >= -2 && box.right <= innerWidth + 2;
    }"""), "two-stock bars must fit 390px without horizontal clipping"
    page.locator(".u-compare-table-details summary").click()
    assert page.locator("#compareResults .u-compare-table").is_visible()
    page.locator(".u-compare-table-details summary").click()
    assert "점수 우열" in page.locator("#compareStatus").inner_text() or (
        "관측 기준일" in page.locator("#compareStatus").inner_text()
    )
    # Users cannot compare a stock to itself or silently reuse a stale result.
    page.locator("#compareCodeB").fill("005930")
    assert page.locator("#compareResults").is_hidden()
    page.locator("#compareRun").click()
    assert "동일한 종목" in page.locator("#compareStatus").inner_text()
    page.locator("#compareReset").click()
    assert page.locator("#compareCodeA").input_value() == ""
    assert page.locator("#compareCodeB").input_value() == ""
    # Only source-dated provisional V3 points are displayed; no V2.2 rank.
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "false"
    assert page.locator(".u-stock").first.locator(".u-card-score").is_visible()
    assert page.locator(".u-stock").first.locator(".u-compact-pillars").is_visible()
    assert page.locator(".u-compact-pillars [data-pillar='volume']").first.is_visible()
    volume = context.request.get(URL + "v3-volume-obv-observed.json").json()
    # New 6pt MA20/60 crossing replaces retired standalone OBV rise.
    # Legacy same-date sources remain a safe 10pt-only UI fallback; they
    # must never be mistaken for 16pt actually observed crossing evidence.
    if volume["schema"] == "v3-current-market-volume-ma-cross-provisional-research-0.2":
        assert volume["maximum_available_point_budget_per_observed_issuer"] == 16
        assert volume["base_available_point_budget_per_observed_issuer"] == 10
        assert volume["ma_cross_unavailable_is_not_zero"] is True
    else:
        assert volume["schema"] == "v3-current-market-volume-obv-provisional-research-0.1"
        assert volume["available_point_budget_per_observed_issuer"] == 16
    assert volume["observed_volume_candidate_issuers"] > 0
    volume_market_day = context.request.get(URL + "data.json").json()["date"]
    assert volume["asof"] <= volume_market_day, "future volume evidence is invalid"
    # The main scorecard must show only the source-observed 10..16/20 contribution.
    # A renamed but empty IAS card is an erroneous rollout.
    shown = page.locator('.u-compact-pillars span[data-pillar="volume"] b').first.inner_text()
    if volume["asof"] == volume_market_day:
        assert shown.strip() != "—", "real observed volume candidate not applied"
    elif shown.strip() != "—":
        # A previous valid issuer observation may remain visible, but must
        # retain its actual date rather than acquire today's price date.
        title = page.locator('.u-compact-pillars span[data-pillar="volume"]').first.get_attribute("title")
        assert volume_market_day not in title, "retained volume evidence was relabeled current"
    assert page.locator(".u-compact-pillars span[data-pillar='technical']").first.is_visible()
    assert page.locator(".u-compact-pillars").first.locator("span[data-pillar]").count() == 5
    data = context.request.get(URL + "data.json").json()
    assert data["version"] == "2.0" and data["status"] == "success"
    # V3.0a acceptance: a matching official-source CSV must visibly restore
    # both partial financial and valuation points on the unified stock card.
    # The 5-factor UI must never silently hide a verified same-day overlay.
    if (observed["asof"] == data["date"]
            and observed["observed_issuer_counts"]["financial"] > 0
            and observed["observed_issuer_counts"]["valuation"] > 0):
        source_csv = context.request.get(URL + "v3-current-observed-all-listed.csv").text()
        source_rows = list(csv.DictReader(StringIO(source_csv.removeprefix(chr(0xfeff)))))
        personal_response = context.request.get(URL + "v3-personal-adjusted-score-summary.json")
        personal_source = personal_response.json() if personal_response.ok else {}
        listed_codes = {item["code"] for item in data["stocks"]}
        selected = next((
            item for item in source_rows
            if item["종목코드"] in listed_codes
            and item["가격자료기준일"] == observed["asof"]
            and item["재무_잠정점수"] != ""
            and item["밸류에이션_잠정점수"] != ""
            and item["재무_산출배점"] != ""
            and item["가치_산출배점"] != ""
        ), None)
        assert selected is not None, "no same-day financial/value research stock to verify"
        page.locator("#stockSearch").fill(selected["종목코드"])
        stock = page.locator(".u-stock")
        assert stock.count() == 1, "direct code search must find observed finance stock"
        for key, value_field, budget_field in (
            ("financial", "재무_잠정점수", "재무_산출배점"),
            ("valuation", "밸류에이션_잠정점수", "가치_산출배점"),
        ):
            cell = stock.locator(f'.u-compact-pillars span[data-pillar="{key}"]')
            eps_value = personal_source.get('valuation_overrides', {}).get(selected['종목코드']) if key == 'valuation' else None
            expected_points = eps_value['points'] if eps_value else float(selected[value_field])
            expected_budget = 20 if eps_value else int(selected[budget_field])
            assert cell.locator("b").inner_text().strip() == (
                f'{expected_points:.1f}'
            ), f"{key} source-observed points missing in rendered card"
            assert f'확보 {expected_budget}/20' in cell.get_attribute("title")
            assert "미검증" in cell.get_attribute("title")
        page.locator("#stockSearch").fill("")
    page.locator("#researchPanel > summary").click()
    assert page.locator("#marketSummary > summary").is_visible()
    page.locator("#marketSummary > summary").click()
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
    page.locator("#dataReadiness > summary").click()
    page.wait_for_function(
        "() => document.getElementById('readinessStatus').textContent.includes('자료 기준일')"
        " || document.getElementById('readinessStatus').textContent.includes('출처 재검증 중')",
        timeout=15000
    )
    readiness = page.locator("#readinessStatus").inner_text()
    if "DART 동기화 대기" in readiness:
        assert dart_lag >= 1
        assert page.locator("#readinessPrice").inner_text() == (
            f"{observed['observed_issuer_counts']['technical']:,} / {observed['listed_total']:,}"
        )
        for field in ("Financial", "Cashflow", "FinancialIssuers",
                      "ValuationIssuers", "Provisional100"):
            assert page.locator("#readiness" + field).inner_text() == "동기화 대기"
        assert page.locator("#readinessCertified").inner_text() == "0종목"
        assert "독립 인증 전" in page.locator("#readinessNotes").inner_text()
    elif "당일 재무 잠정근거 확보" in readiness:
        assert dart_lag >= 1
        assert page.locator("#readinessFinancial").inner_text() == "동기화 대기"
        for field, key in (("Price", "technical"), ("FinancialIssuers", "financial"),
                           ("ValuationIssuers", "valuation")):
            assert page.locator("#readiness" + field).inner_text() == (
                f"{observed['observed_issuer_counts'][key]:,} / {observed['listed_total']:,}"
            )
        assert page.locator("#readinessCashflow").inner_text() == (
            f"{independent['observed_source_depth']['three_year_receipt_matched_fcf_issuers']:,} / "
            f"{observed['listed_total']:,}"
        )
        assert page.locator("#readinessProvisional100").inner_text() == (
            f"{observed['provisional_complete_100point_observation_count']:,}종목 · 미인증"
        )
        assert page.locator("#readinessCertified").inner_text() == "0종목"
        assert "DART 4개년 연도별 집계" in page.locator("#readinessNotes").inner_text()
        assert "독립 인증 전" in page.locator("#readinessNotes").inner_text()
    elif "자료 기준일" in readiness:
        assert dart_lag == 0
        assert observed["listed_total"] == evidence["listed_total"]
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
        assert page.locator("#readinessFinancialIssuers").inner_text() == (
            f"{observed['observed_issuer_counts']['financial']:,} / {observed['listed_total']:,}"
        )
        assert page.locator("#readinessValuationIssuers").inner_text() == (
            f"{observed['observed_issuer_counts']['valuation']:,} / {observed['listed_total']:,}"
        )
        assert page.locator("#readinessProvisional100").inner_text() == (
            f"{observed['provisional_complete_100point_observation_count']:,}종목 · 미인증"
        )
        assert page.locator("#readinessCertified").inner_text() == "0종목"
        # The market-price snapshot can advance before the independently
        # replayed issuer coverage snapshot (including on the PR merge ref).
        # In that transition the UI must fail closed, not show yesterday's
        # nine-factor audit as today's observations.
        current_observed = (
            observed["asof"] == data["date"]
            and observed["listed_total"] == data["eligible_count"]
        )
        if current_observed and observed.get("financial_valuation_component_observed_issuers"):
            subfactors = page.locator("#fiveAreaCoverage details.u-subfactor-breakdown")
            subfactors.first.wait_for(state="attached", timeout=20000)
            assert subfactors.count() == 1
            subfactors.locator("summary").click()
            assert subfactors.locator("tbody tr").count() == 9
            assert "미확보 기업" in subfactors.inner_text()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            ), "nested factor audit mobile horizontal overflow"
            subfactors.locator("summary").click()
        elif not current_observed:
            assert page.locator("#fiveAreaCoverage details.u-subfactor-breakdown").count() == 0
            assert "자료 검증 후 표시" in page.locator("#fiveAreaCoverage").inner_text()
    else:
        # Market-close transition may leave legitimately mismatched snapshot
        # dates; the UI must show unknown rather than silently merge dates.
        for field in ("Financial", "Price", "Cashflow", "FinancialIssuers", "ValuationIssuers", "Provisional100", "Certified"):
            assert page.locator("#readiness" + field).inner_text() == "미확인"
    assert page.get_by_text("과거 파생자료 수집 실행 기록 확인 ↗").is_visible()
    page.locator("#dataReadiness > summary").click()
    assert not page.locator("#dataReadiness").evaluate("(el) => el.open")
    # Historical OpenDART receipts have their own private-source checkpoint
    # timeline, never mixed with the current market-day score readiness.
    assert not page.locator("#dartHistoryProgress").evaluate("(el) => el.open")
    page.locator("#dartHistoryProgress > summary").click()
    page.wait_for_function(
        "() => document.getElementById('dartHistoryStatus').textContent.includes('수집 기간')"
        " || document.getElementById('dartHistoryStatus').textContent.includes('목록 수집')"
        " || document.getElementById('dartHistoryStatus').textContent.includes('재검증 중')",
        timeout=15000
    )
    history_status = page.locator("#dartHistoryStatus").inner_text()
    if "수집 기간" in history_status or "목록 수집" in history_status:
        assert page.locator("#dartHistoryMonths").inner_text().endswith("개월")
        assert page.locator("#dartHistoryPages").inner_text().endswith("페이지")
        assert "접수 메타데이터" in page.locator("#dartHistoryReceipts").inner_text()
        if "목록 수집" in history_status:
            assert page.locator("#dartHistoryMonths").inner_text() == "120 / 120개월"
            assert "별도 검증 대상" in page.locator("#dartHistoryNotes").inner_text()
            assert "전수검증 완료" in history_status or "전수검증 미확인" in history_status
        else:
            assert "연구용" in history_status
            assert "인증 전" in page.locator("#dartHistoryNotes").inner_text()
    else:
        for field in ("Months", "Pages", "Receipts"):
            assert page.locator("#dartHistory" + field).inner_text() == "미확인"
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), "historical DART progress panel mobile overflow"
    page.locator("#dartHistoryProgress > summary").click()
    assert not page.locator("#dartHistoryProgress").evaluate("(el) => el.open")
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
    assert page.locator(".u-card-score").first.is_visible()
    assert "산출 배점" in page.locator(".u-card-coverage").first.inner_text()
    page.locator(".u-stock").filter(
        has=page.locator(".u-card-price > small").filter(has_text="종가 ·")
    ).first.click()
    assert page.locator("#detail").is_visible()
    details = page.locator("#detail").inner_text()
    assert "일봉 · 스윙 관찰" in details and "주봉 · 중기/장기 관찰" in details
    assert "5영역 잠정 점수" in details and "미인증" in details
    assert "손실 위험" in details
    page.locator("#detailClose").click()
    personal_value = page.evaluate("""async () => {
        const r=await fetch('v3-personal-adjusted-score-summary.json',{cache:'no-store'});
        return r.ok?await r.json():null;
    }""")
    price_codes = {row['code'] for row in data['stocks']}
    eps_candidates = [code for code in (personal_value or {}).get('valuation_overrides', {})
                      if code in price_codes]
    if eps_candidates:
        page.locator('#stockSearch').fill(eps_candidates[0])
        page.locator('.u-stock').first.click()
        value_detail = page.locator('#detail .u-score-row[data-pillar="valuation"]')
        if not value_detail.evaluate('(el) => el.open'):
            value_detail.locator('summary').click()
        assert '가치20 개편' in value_detail.inner_text()
        assert 'EPS 성장' in value_detail.inner_text() and 'EPS 안정' in value_detail.inner_text()
        assert '2023~2025 기본 EPS' in value_detail.inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.locator('#detailClose').click()
        page.locator('#stockSearch').fill('')
    for scope in ("patterns","rsi","quant","discover"):
        page.locator(f'[data-tab="{scope}"]').click()
        assert page.locator(f'[data-tab="{scope}"]').get_attribute("aria-pressed") == "true"
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), f"horizontal overflow in {scope}"
    # Real narrow-screen geometry, not merely a 390px root scrollWidth:
    # an overflowing toolbar or 5 tiny columns can push visible controls
    # beyond the right edge even when the document itself clips overflow.
    for viewport_width in (320, 360, 390, 430, 600, 768):
        page.set_viewport_size({"width": viewport_width, "height": 844})
        mobile_geometry = page.evaluate("""() => {
            const w = window.innerWidth;
            const visible = el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
            };
            const selectors = [
                '.u-wrap', '.u-header-inner', '.u-header-actions',
                '.u-header-actions > *', '.u-live-updates',
                '.u-live-updates-grid', '.u-live-updates-grid > div',
                '.u-intro', '.u-intro > summary',
                '.u-controls', '.u-quick-search', '#stockSearch', '.u-tabs',
                '.u-results', '.u-list-area', '.u-stock',
                '.u-stock .u-compact-pillars', '.u-stock .u-compact-pillars span'
            ];
            const over = [];
            for (const selector of selectors) {
                for (const el of document.querySelectorAll(selector)) {
                    if (!visible(el)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.left < -2 || rect.right > w + 2) {
                        over.push({selector, left: Math.round(rect.left),
                            right: Math.round(rect.right)});
                    }
                }
            }
            const actions = [...document.querySelectorAll(
                '.u-header-actions > *')].filter(visible);
            return {
                width: w,
                docWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
                overflow: over,
                actionCount: actions.length,
                factorCount: document.querySelectorAll(
                    '.u-stock:first-child .u-compact-pillars span').length,
            };
        }""")
        assert mobile_geometry["width"] == viewport_width, mobile_geometry
        assert mobile_geometry["docWidth"] <= viewport_width + 1, mobile_geometry
        assert mobile_geometry["bodyWidth"] <= viewport_width + 1, mobile_geometry
        assert not mobile_geometry["overflow"], mobile_geometry
        assert mobile_geometry["actionCount"] == 2, mobile_geometry
        assert mobile_geometry["factorCount"] == 5, mobile_geometry
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#industryResearch > summary").click()
    industry_geometry = page.evaluate("""() => {
        const card = document.querySelector('#industryResearch');
        const items = [...card.querySelectorAll('.u-scroller')];
        return {
            cardRight: card.getBoundingClientRect().right,
            wrappers: items.map(el => ({
                left: el.getBoundingClientRect().left,
                right: el.getBoundingClientRect().right,
                scrollable: el.scrollWidth >= el.clientWidth,
            })),
            viewport: window.innerWidth,
        };
    }""")
    assert industry_geometry["cardRight"] <= industry_geometry["viewport"] + 2
    assert all(row["left"] >= -2 and row["right"] <= industry_geometry["viewport"] + 2
               for row in industry_geometry["wrappers"]), industry_geometry
    page.locator("#industryResearch summary").click()
    # Existing exporter remains downloadable without moving to legacy UI.
    with page.expect_download(timeout=30000) as dl:
        page.locator(".u-header-more > summary").click()
        page.get_by_role("link", name="전체 엑셀", exact=True).click()
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
