"""Mobile-friendly, STATIC, LOCAL-ONLY v3 research preview (not live website).

Renders v3_offline_workbench output after strict research authorization checks.
No JavaScript, network, dashboard publication, orders or provider calls.
Store only in a private directory OUTSIDE this public Git repository.
"""
from __future__ import annotations

import argparse
from html import escape
import json
import os
from pathlib import Path
import stat
import tempfile

from v3_offline_workbench import (
    MAX_INPUT_BYTES, WorkbenchError, demo_bundle, run_workbench
)

REPO = Path(__file__).resolve().parent
DEFAULT_REPORT = Path.home() / ".local/share/korean-stock-screener/v3/private-research-preview.html"
PILLARS = [
    ("ias", "기관·외국인 매집", 25),
    ("financial", "재무·현금흐름", 20),
    ("valuation", "기업가치", 15),
    ("technical", "일봉·주봉 기술", 15),
    ("industry", "업종 사이클", 15),
    ("momentum", "모멘텀", 10),
]


class MobileReportError(ValueError):
    """Static reason codes only, no private financial or price data."""


def _esc(v):
    return escape(str(v), quote=True)


def _points(v):
    if v is None:
        return "미확인"
    if type(v) not in (int, float) or not -1e9 <= v <= 1e9:
        raise MobileReportError("invalid_research_score")
    return f"{v:.1f}"


def render_mobile_report(report, *, label="SYNTHETIC_DEMO"):
    if (not isinstance(report, dict)
            or report.get("schema") != "v3-offline-executable-workbench-0.1"
            or report.get("v3_release_ready") is not False
            or report.get("confirmed_buy") is not False
            or report.get("confirmed_stock_ranks") is not None
            or report.get("recommended_positions") is not None
            or report.get("expected_returns") is not None
            or label not in ("SYNTHETIC_DEMO", "UNVERIFIED_LOCAL_INPUT")):
        raise MobileReportError("unverified_investment_report_cannot_be_rendered")
    stocks = report.get("stocks_in_code_order_not_ranked")
    if not isinstance(stocks, list) or len(stocks) > 20000:
        raise MobileReportError("invalid_research_stock_rows")
    if any(not isinstance(s, dict) or s.get("confirmed_buy") is not False
           or s.get("confirmed_rank") is not None
           or s.get("confirmed_score") is not None for s in stocks):
        raise MobileReportError("confirmed_investment_claim_forbidden")
    # It is visually obvious that full candidate score is provisional.
    header = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; script-src 'none'; base-uri 'none'; form-action 'none'">
<title>v3 비공개 연구용 평가 미리보기</title>
<style>
:root{font-family:system-ui,-apple-system,Arial,sans-serif;background:#f5f7fb;color:#172236}
*{box-sizing:border-box}body{margin:0;padding:0}
main{max-width:970px;margin:auto;padding:20px 14px 56px}
h1{font-size:1.55rem;line-height:1.35;margin:0 0 8px}h2{font-size:1.05rem;margin:26px 0 12px}
p{line-height:1.65}.notice{background:#fff4e8;border-left:5px solid #c65c1c;padding:14px;border-radius:9px;font-size:.91rem}
summary{cursor:pointer;font-weight:650;display:flex;gap:10px;align-items:center;justify-content:space-between;list-style:none}
summary::-webkit-details-marker{display:none}
.meta{color:#526179;font-size:.83rem}.topline{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}
.tag{border:1px solid #ced8e6;background:white;border-radius:999px;padding:6px 11px;font-size:.83rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,370px),1fr));gap:12px}
article,.panel{background:#fff;border:1px solid #dbe1ec;border-radius:12px;padding:14px;overflow-wrap:anywhere}
article{min-width:0}article details{margin:0}.scores{margin:13px 0 2px;display:grid;grid-template-columns:1fr auto;gap:7px 10px;font-size:.86rem}
.scores dt{color:#4f5d74}.scores dd{margin:0;font-variant-numeric:tabular-nums}
.big{font-weight:750;font-size:1.08rem}.soft{color:#607087;font-size:.76rem}.dot{background:#edf1f8;border-radius:8px;padding:5px 9px}
footer{font-size:.79rem;color:#53647c;margin-top:24px}
@media(max-width:420px){main{padding:16px 10px}h1{font-size:1.32rem}.grid{display:block}article{margin-bottom:10px}}
</style></head><body><main>"""
    asof = _esc(report.get("asof", ""))
    count = len(stocks)
    covered = int(report.get("fully_covered_six_pillar_research_only", 0))
    overlap = report.get("factor_overlap_diagnostics_research_only") or {}
    warnings = int(overlap.get("research_redundancy_warning_count", 0))
    lead = (f"<h1>v3 주식 분석 · 로컬 연구 미리보기</h1>"
            f"<p class='meta'>기준일 {asof} · 데이터: {_esc(label)} · 자동 매매와 분리</p>"
            "<div class='notice'><strong>실전 매수·매도 추천이 아닙니다.</strong> "
            "표시된 숫자는 비인증 연구 후보값이며 공식 KRX·DART 실제 원본, "
            "과거 상장폐지 포함 분석, 통계적 성능 검증이 완료되지 않았습니다. "
            "확정 종목 순위·예상 수익률·매수 판정은 제공하지 않습니다.</div>"
            f"<div class='topline'><span class='tag'>연구 대상 {count}개</span>"
            f"<span class='tag'>6영역 자료 완비 {covered}개</span>"
            f"<span class='tag'>중복 지표 경고 {warnings}건</span></div>"
            "<h2>연구 대상 종목 (점수순 정렬 아님)</h2>")
    cards = []
    for stock in sorted(stocks, key=lambda x: (
            str(x.get("market")), str(x.get("code")))):
        values = stock.get("pillar_research_candidates")
        if not isinstance(values, dict):
            raise MobileReportError("missing_six_pillar_components")
        # Reject numeric scores that claim more than their agreed max.
        listing = []
        for key, korean, maximum in PILLARS:
            value = values.get(key)
            if value is not None and (type(value) not in (int, float)
                                       or not 0 <= value <= maximum):
                raise MobileReportError("out_of_contract_pillar_candidate")
            listing.append(f"<dt>{_esc(korean)} / {maximum}</dt><dd>{_points(value)}</dd>")
        candidate = stock.get("candidate_full_total_research_only")
        coverage = stock.get("candidate_coverage_pct")
        if type(coverage) not in (float, int) or not 0 <= coverage <= 100:
            raise MobileReportError("invalid_candidate_coverage")
        if candidate is not None and (
                type(candidate) not in (float, int) or not 0 <= candidate <= 100
                or coverage != 100):
            raise MobileReportError("invalid_full_research_candidate")
        missing = stock.get("missing_features", [])
        if not isinstance(missing, list):
            raise MobileReportError("invalid_missing_feature_names")
        summary = (f"<span>{_esc(stock.get('market', ''))} · "
                   f"{_esc(stock.get('code', ''))}</span>"
                   f"<span class='dot'>자료 {coverage:.0f}%</span>")
        cards.append(
            "<article><details><summary>" + summary + "</summary>"
            "<p class='soft'>잠정 연구 합계: <span class='big'>" +
            _points(candidate) + "</span> / 100 · 확정순위 없음</p>"
            "<dl class='scores'>" + "".join(listing) + "</dl>" +
            f"<p class='soft'>원시 지표 결측 {len(missing)}개 · "
            "손절/무효화 가격은 이 점수에 반영되지 않음</p>"
            "</details></article>"
        )
    risk = report.get("hypothetical_portfolio_risk_only")
    risk_html = "<p class='meta'>입력된 가상 포트폴리오 계획 없음. 자동 비중 추천 기능은 사용하지 않습니다.</p>"
    if risk is not None:
        if not isinstance(risk, dict) or risk.get("trade_approval") is not False:
            raise MobileReportError("unapproved_portfolio_claim")
        risk_html = (
            "<p>입력한 가정에 따른 총자산 대비 손절·갭 하락 손실 "
            f"<strong>{_points(risk.get('total_hypothetical_gap_loss_of_nav_pct'))}%</strong>"
            f" · 위험예산 판정 {_esc(risk.get('stress_budget_status'))}</p>"
            "<p class='soft'>가정한 손절 체결이 보장되지 않으며 실제 하락폭은 더 클 수 있습니다.</p>"
        )
    tail = (
        f"<h2>가상 포트폴리오 하방 위험</h2><section class='panel'>{risk_html}</section>"
        "<h2>향후 공식 API 연결 조건</h2><section class='panel'>"
        "<p>거래소 정규장 가격·수급, 당시 유통주식수, 공시 정정 이력, 업종별 과거 구성, "
        "기업행위 검증과 표본외 보정이 완료될 때까지 비인증 연구 상태를 유지합니다.</p>"
        "</section><footer>로컬 오프라인 HTML · 외부 자바스크립트·네트워크 요청 없음 · "
        "코스피/코스닥 합산 매수 순위 없음</footer></main></body></html>"
    )
    return header + lead + "<div class='grid'>" + "".join(cards) + "</div>" + tail


def _output_path(path):
    if not isinstance(path, (str, Path)):
        raise MobileReportError("invalid_private_preview_path")
    target = Path(path).expanduser()
    if target.suffix.lower() != ".html":
        raise MobileReportError("html_preview_extension_required")
    if target.is_symlink() or target.parent.is_symlink():
        raise MobileReportError("unsafe_preview_symlink")
    resolved = target.resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise MobileReportError("publishing_in_public_repo_forbidden")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        if target.parent.stat().st_mode & 0o077:
            raise MobileReportError("private_preview_directory_open")
        if target.exists() and target.stat().st_mode & 0o077:
            raise MobileReportError("private_preview_file_open")
    return target


def save_mobile_report(report, *, output_path=DEFAULT_REPORT,
                       label="SYNTHETIC_DEMO"):
    html = render_mobile_report(report, label=label)
    target = _output_path(output_path)
    fd, temporary = tempfile.mkstemp(
        prefix=".v3-report-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(html)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"status": "private_research_html_created",
            "stock_count": len(report["stocks_in_code_order_not_ranked"]),
            "confirmed_buy": False, "public_pages_changed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private local v3 mobile research preview")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true")
    src.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    try:
        if args.demo:
            batch, label = demo_bundle(), "SYNTHETIC_DEMO"
        else:
            if not args.input.is_file() or args.input.stat().st_size > MAX_INPUT_BYTES:
                raise MobileReportError("invalid_local_research_input")
            batch = json.loads(args.input.read_text(encoding="utf-8"))
            label = "UNVERIFIED_LOCAL_INPUT"
        report = run_workbench(batch)
        result = save_mobile_report(report, output_path=args.output, label=label)
    except (MobileReportError, WorkbenchError, json.JSONDecodeError,
            OSError, UnicodeError):
        print('{"status":"private_research_report_blocked","confirmed_buy":false}')
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
