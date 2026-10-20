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
    page.locator("#statAnalyzed").wait_for()
    page.get_by_role("button", name="전 종목", exact=True).click()
    page.locator(".u-stock").first.wait_for(timeout=45000)
    assert page.locator("#sort").input_value() == "score", "default sort must follow visible scores"
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "true"
    # Stock rank is computed against the entire priced market, not
    # an arbitrary upstream list or the order of filtered watch-candidates.
    labels=page.locator(".u-rank-number").all_inner_texts()
    initial_ranks=[int(v.rstrip("위")) for v in labels]
    assert initial_ranks and initial_ranks == sorted(initial_ranks), initial_ranks
    assert initial_ranks[0] == 1
    assert page.locator(".u-scoresix").first.get_by_text("V3 합계").is_visible()
    assert page.locator(".u-scoresix").first.get_by_text("미산출").is_visible()
    data = context.request.get(URL + "data.json").json()
    assert data["version"] == "2.0" and data["status"] == "success"
    displayed = int(page.locator("#statAnalyzed").inner_text().replace(",", ""))
    assert 0 < displayed <= data["candidate_count"]
    assert page.get_by_text("V3 6영역 종합점수").is_visible()
    assert page.locator("#statV3").inner_text().strip() == "미완성"
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), "unified mobile horizontal overflow"
    code = page.locator(".u-stock").first.locator(".u-stock-name small").inner_text().split(" · ")[0]
    search = page.locator("#stockSearch")
    search.fill(code)
    assert page.locator(".u-stock").count() == 1
    search.fill("__unknown_stock__")
    assert page.locator(".u-stock").count() == 0
    assert page.get_by_text("해당 조건을 만족하는 종목이 없습니다.", exact=False).is_visible()
    search.fill("")
    page.locator("#top100Only").click()
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "false"
    assert "전체 시장" in page.locator("#resultCount").inner_text()
    page.locator("#top100Only").click()
    assert page.locator("#top100Only").get_attribute("aria-pressed") == "true"
    page.locator("#sort").select_option("score")
    scores = page.locator(".u-stock-main span:nth-child(2) b").all_inner_texts()
    scores = [float(x) for x in scores]
    assert scores == sorted(scores, reverse=True), "old observed scores must be explicitly ordered"
    page.locator(".u-stock").first.click()
    assert page.locator("#detail").is_visible()
    details = page.locator("#detail").inner_text()
    assert "일봉 · 스윙 관찰" in details and "주봉 · 중기/장기 관찰" in details
    assert "V3 통합 100점" in details and "미산출" in details
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
                       "legacy sort explicitly labeled", "downside first",
                       "same-screen details", "Excel export", "horizontal overflow",
                       "no JavaScript errors"]
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    browser.close()
