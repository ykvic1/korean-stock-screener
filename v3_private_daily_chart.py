"""Local-only daily candlestick SVG with optional MA 5/20/60/120/240.

Consumes the Step 7 PRIVATE observed source, reaudited before each read.
Renders no network/CDN resources, makes no scoring or trade claim, and does
NOT publish licensed market data to public GitHub Pages. The renderer holds
prices in memory; export requires explicit manual private-local consent.
"""
from __future__ import annotations

import argparse
from datetime import date
from html import escape
import json
import math
import os
from pathlib import Path

from v3_krx_private_price_bridge import build_private_price_panels, PrivatePanelError
from v3_optional_technical_research import (
    analyze_observed_panel, PartialTechnicalError, MA_WINDOWS,
    MAX_ALLOWED_UNADJUSTED_CLOSE_JUMP,
)
from v3_krx_private_backfill_plan import calendar_weekday_candidates, BackfillPlanError
from v3_krx_session_candidates import candidate_dates, CandidateCalendarError
from v3_local_private_store import _validate_path, PrivateStoreError, ROOT

COLORS = {
    5: "#0c9b72", 20: "#c78b18", 60: "#257bcb",
    120: "#815bd1", 240: "#d14970",
}
CHART_SESSIONS = 260


class PrivateDailyChartError(ValueError):
    """Fixed error codes only, no private data/path/credentials."""


def build_observed_daily_chart(panel, archived_days, *, max_sessions=CHART_SESSIONS):
    """Prepare candle + non-bridging SMA values for in-memory SVG rendering.

    Every missing/invalid date resets SMA history. A >35% close-to-close
    discontinuity also resets, but is NOT labelled a corporate action.
    """
    if (type(max_sessions) is not int or not 30 <= max_sessions <= 500
            or not isinstance(archived_days, list)
            or archived_days != sorted(set(archived_days))):
        raise PrivateDailyChartError("invalid_daily_chart_window")
    try:
        analyzed = analyze_observed_panel(panel, archived_days)
    except PartialTechnicalError:
        raise PrivateDailyChartError("private_technical_preflight_failed") from None
    raw = panel.get("observed_bars", [])
    permitted = set(archived_days)
    if any(row["date"] not in permitted for row in raw):
        raise PrivateDailyChartError("bar_outside_archive_calendar")
    observed = {row["date"]: row for row in raw}
    timeline = []
    closes = []
    previous = None
    for day in archived_days:
        row = observed.get(day)
        if row is None:
            closes.clear()
            previous = None
            timeline.append({"date": day, "candle": None, "ma": {
                str(n): None for n in MA_WINDOWS}})
            continue
        candle = {k: float(row[k]) for k in ("open", "high", "low", "close")}
        if previous is not None and abs(
                candle["close"] / previous - 1) > MAX_ALLOWED_UNADJUSTED_CLOSE_JUMP:
            closes.clear()
        closes.append(candle["close"])
        previous = candle["close"]
        if len(closes) > max(MA_WINDOWS):
            closes.pop(0)
        averages = {
            str(n): round(sum(closes[-n:]) / n, 4) if len(closes) >= n else None
            for n in MA_WINDOWS
        }
        timeline.append({"date": day, "candle": candle, "ma": averages})
    latest_ma = {str(n): analyzed["indicators"][f"daily_ma{n}"] for n in MA_WINDOWS}
    if timeline:
        for n in MA_WINDOWS:
            last_from_curve = timeline[-1]["ma"][str(n)]
            if last_from_curve != latest_ma[str(n)]:
                raise PrivateDailyChartError("indicator_and_chart_disagree")
    return {
        "schema": "v3-local-private-daily-chart-0.1",
        "date": archived_days[-1] if archived_days else None,
        "latest_ma": latest_ma, "timeline": timeline[-max_sessions:],
        "requested_days": len(archived_days),
        "rendered_days": min(len(timeline), max_sessions),
        "last_observation_available": bool(timeline and timeline[-1]["candle"]),
        "missing_ma_windows": [n for n in MA_WINDOWS if latest_ma[str(n)] is None],
        "warning": analyzed["warning"],
        "source_scope": "KRX_EOD_AGGREGATE_UNVERIFIED",
        "official_regular_session_verified": False,
        "adjusted_price_verified": False,
        "eligible_for_v3_score": False, "confirmed_buy": False,
    }


def render_private_daily_chart_html(chart, *, stock_label="비공개 종목"):
    """Self-contained offline HTML + SVG, never a public recommendation."""
    if (not isinstance(chart, dict)
            or chart.get("schema") != "v3-local-private-daily-chart-0.1"
            or chart.get("eligible_for_v3_score") is not False
            or chart.get("confirmed_buy") is not False
            or chart.get("official_regular_session_verified") is not False
            or chart.get("adjusted_price_verified") is not False
            or type(stock_label) is not str or not 1 <= len(stock_label) <= 60):
        raise PrivateDailyChartError("invalid_private_chart_render_input")
    rows = chart.get("timeline")
    if not isinstance(rows, list) or len(rows) > 500:
        raise PrivateDailyChartError("invalid_private_chart_points")
    # SVG uses no external images, scripts or interactive handlers.
    left, right, top, bottom = 72, 1140, 50, 470
    values = []
    for row in rows:
        candle = row["candle"]
        if candle is not None:
            values += [candle["low"], candle["high"]]
        values += [v for v in row["ma"].values() if v is not None]
    low, high = (min(values), max(values)) if values else (0.0, 1.0)
    pad = (high - low) * 0.07 if high > low else max(high * 0.02, 1)
    low, high = low - pad, high + pad
    span = high - low
    count = len(rows)
    step = (right - left) / max(1, count)
    width = min(7, max(1.0, step * 0.62))
    def xx(i):
        return left + (i + 0.5) * step
    def yy(v):
        return bottom - (v - low) / span * (bottom - top)
    ink = []
    for n in range(6):
        price = low + (high - low) * n / 5
        y = yy(price)
        ink.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="#e8edf5"/>')
        ink.append(f'<text x="62" y="{y+4:.2f}" text-anchor="end" font-size="12" fill="#526174">{price:,.0f}</text>')
    for i, row in enumerate(rows):
        candle = row["candle"]
        if candle is None:
            continue
        x = xx(i)
        color = "#c23f4c" if candle["close"] >= candle["open"] else "#347ac8"
        ink.append(f'<line x1="{x:.2f}" y1="{yy(candle["high"]):.2f}" x2="{x:.2f}" '
                   f'y2="{yy(candle["low"]):.2f}" stroke="{color}" stroke-width="1.15"/>')
        first = min(yy(candle["open"]), yy(candle["close"]))
        bar_h = max(abs(yy(candle["open"])-yy(candle["close"])), 1)
        ink.append(f'<rect x="{x-width/2:.2f}" y="{first:.2f}" width="{width:.2f}" '
                   f'height="{bar_h:.2f}" fill="{color}"/>')
    for n in MA_WINDOWS:
        color = COLORS[n]
        points = []
        def emit():
            if len(points) >= 2:
                ink.append('<polyline points="' + ' '.join(points) +
                           f'" fill="none" stroke="{color}" stroke-width="2" '
                           'stroke-linejoin="round" stroke-linecap="round"/>')
            elif len(points) == 1:
                x, y = points[0].split(",")
                ink.append(f'<circle cx="{x}" cy="{y}" r="2.8" fill="{color}"/>')
            points.clear()
        for i, row in enumerate(rows):
            value = row["ma"][str(n)]
            if value is None:
                emit()
            else:
                points.append(f'{xx(i):.2f},{yy(value):.2f}')
        emit()
    for i in sorted({0, max(0, count//4), max(0, count//2),
                     max(0, count*3//4), max(0, count-1)}):
        if rows:
            ink.append(f'<text x="{xx(i):.2f}" y="496" text-anchor="middle" '
                       f'font-size="12" fill="#526174">{escape(rows[i]["date"])}</text>')
    status = ''.join(
        f'<span class="ma"><i style="background:{COLORS[n]}"></i>'
        f'일봉 {n}일선 '
        f'<b>{chart["latest_ma"][str(n)]:,.2f}</b></span>'
        if chart["latest_ma"][str(n)] is not None else
        f'<span class="ma muted"><i style="background:{COLORS[n]}"></i>'
        f'일봉 {n}일선 <b>자료 부족</b></span>'
        for n in MA_WINDOWS
    )
    warning = ("<p class='alert'>원본 종가가 35% 이상 단절되어 이전 구간의 이평선은 "
               "이어 그리지 않았습니다. 원인은 확인되지 않았습니다.</p>"
               if chart["warning"] else "")
    date_label = escape(chart["date"] or "관측일 없음", quote=True)
    title = escape(stock_label, quote=True)
    return ("""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; script-src 'none'; form-action 'none'; base-uri 'none'">
<title>v3 일봉 연구 차트 (비공개)</title><style>
:root{font-family:system-ui,Arial,sans-serif;background:#f4f6fa;color:#1a283d}
*{box-sizing:border-box}main{max-width:1260px;margin:auto;padding:18px}
h1{font-size:1.4rem}.panel{background:white;border:1px solid #d6e0ed;border-radius:12px;padding:16px}
svg{display:block;width:100%;height:auto;min-height:230px}
.meta,.notice{color:#5a6778;font-size:.85rem;line-height:1.6}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin:15px 0}
.ma{display:inline-flex;align-items:center;gap:6px;padding:7px 9px;border:1px solid #d8dfeb;border-radius:8px;font-size:.84rem}
.ma i{display:inline-block;width:18px;height:3px;border-radius:4px}
.muted{color:#68768b}.alert{color:#a84520;font-size:.85rem}
@media(max-width:550px){main{padding:9px}.panel{padding:8px}h1{font-size:1.1rem}}
</style></head><body><main><h1>v3 비공개 일봉 · """ + title +
            "</h1><p class='meta'>원본 관측 마지막 날 " + date_label +
            " · 이동평균선 5 · 20 · 60 · 120 · 240일</p>"
            "<div class='panel'><div class='legend'>" + status +
            '</div><svg role="img" aria-label="일봉 캔들과 5, 20, 60, 120, 240일 이동평균선" '
            'viewBox="0 0 1200 520" xmlns="http://www.w3.org/2000/svg">'
            + ''.join(ink) + '</svg>' + warning +
            "<p class='notice'>**검증 전 연구용 관측 차트.** KRX 누적거래량의 정규장 전용 여부, "
            "수정주가, 상장·상장폐지 이력 및 종목 식별이 독립 인증되지 않았습니다. "
            "자료 부족 이평선은 임의의 0이나 추정값으로 그리지 않습니다. "
            "투자 점수·추천·매매 주문에 사용하지 않습니다.</p></div></main></body></html>")


def save_private_chart_html(*, output_path, private_db, html, acknowledge=False):
    """Only explicit opt-in local write in the already private DB directory."""
    if acknowledge is not True:
        raise PrivateDailyChartError("explicit_local_chart_retention_ack_required")
    if not isinstance(output_path, (str, Path)) or type(html) is not str:
        raise PrivateDailyChartError("invalid_private_output")
    source = Path(private_db).expanduser()
    target = Path(output_path).expanduser()
    if target.suffix != ".html" or target.is_symlink() or target.parent.is_symlink():
        raise PrivateDailyChartError("unsafe_private_chart_output")
    try:
        _validate_path(source, create=False)
    except PrivateStoreError:
        raise PrivateDailyChartError("private_database_permissions_invalid") from None
    if (target.resolve().parent != source.resolve().parent
            or target.resolve() == source.resolve()
            or ROOT == target.resolve() or ROOT in target.resolve().parents):
        raise PrivateDailyChartError("chart_must_stay_beside_private_database")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except OSError:
        raise PrivateDailyChartError("private_chart_cannot_be_created") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(html)
    except (OSError, UnicodeError):
        target.unlink(missing_ok=True)
        raise PrivateDailyChartError("private_chart_write_failed") from None
    return {"status": "private_chart_saved_locally", "published": False}


def main(argv=None):
    p = argparse.ArgumentParser(description="Offline private daily OHLC chart")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--market", choices=("KOSPI", "KOSDAQ"), required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--calendar-source", choices=("weekdays", "xkrx"), default="xkrx")
    p.add_argument("--private-db", required=True)
    p.add_argument("--private-html", help="Explicit private local HTML output only")
    p.add_argument("--ack-local-chart-retention", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.private_html and not args.ack_local_chart_retention:
            raise PrivateDailyChartError("explicit_local_chart_retention_ack_required")
        bundle = build_private_price_panels(
            start=args.start, end=args.end, private_db=args.private_db,
            targets=[{"market": args.market, "code": args.code}],
            calendar_source=args.calendar_source)
        weekdays = calendar_weekday_candidates(args.start, args.end)
        candidates = candidate_dates(weekdays, source=args.calendar_source)["dates"]
        count = bundle["summary"]["archived_two_market_pairs"]
        panel = bundle["panels"][(args.market, args.code)]
        chart = build_observed_daily_chart(panel, candidates[:count])
        saved = False
        if args.private_html:
            html = render_private_daily_chart_html(chart, stock_label=args.code)
            save_private_chart_html(output_path=args.private_html,
                                    private_db=args.private_db,
                                    html=html, acknowledge=True)
            saved = True
        print(json.dumps({
            "schema": chart["schema"],
            "mode": "local_private_offline_research",
            "actual_api_calls": 0, "chart_saved_only_in_private_directory": saved,
            "rendered_sessions": chart["rendered_days"],
            "missing_ma_windows": chart["missing_ma_windows"],
            "daily_ma120_available": chart["latest_ma"]["120"] is not None,
            "daily_ma240_available": chart["latest_ma"]["240"] is not None,
            "v3_release_ready": False, "confirmed_buy": False,
        }, sort_keys=True))
        return 0
    except (PrivatePanelError, PrivateDailyChartError, BackfillPlanError,
            CandidateCalendarError, KeyError, PartialTechnicalError):
        print(json.dumps({
            "status": "blocked", "reason": "private_chart_failed",
            "actual_api_calls": 0, "v3_release_ready": False,
            "confirmed_buy": False
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
