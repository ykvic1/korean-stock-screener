"""100% synthetic Step10 daily MA120/240 candlestick overlay and privacy tests."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from v3_optional_technical_research import analyze_observed_panel
from v3_private_daily_chart import (
    build_observed_daily_chart, render_private_daily_chart_html,
    save_private_chart_html, PrivateDailyChartError, main,
)
from v3_local_private_store import store_pair


def weekdays(count, start="2025-05-01"):
    result=[]
    cursor=date.fromisoformat(start)
    while len(result)<count:
        if cursor.weekday()<5:
            result.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    return result


def panel(days, *, missing=(), closes=None):
    bars=[]
    for i, day in enumerate(days):
        if day in missing:
            continue
        p = (closes[i] if closes is not None else 100+i)
        bars.append({
            "date":day, "open":p, "high":p+3, "low":p-3, "close":p,
            "aggregate_acc_trdvol_observed":500,
            "source_scope":"KRX_EOD_AGGREGATE_UNVERIFIED",
            "corporate_action_adjustment_verified":False,
            "issuer_identity_verified":False,
            "regular_only_volume_verified":False,
        })
    return {"observed_bars":bars}


def raw(day, code):
    return json.dumps({"OutBlock_1":[{
        "BAS_DD":day.replace("-",""),"ISU_CD":code,
        "TDD_OPNPRC":"100","TDD_HGPRC":"110",
        "TDD_LWPRC":"90","TDD_CLSPRC":"103","ACC_TRDVOL":"300"
    }]}).encode()


class PrivateDailyChartTests(unittest.TestCase):
    def test_ma120_and_ma240_exact_availability_without_weekly_requirement(self):
        for count in (119, 120, 239, 240, 260):
            with self.subTest(count=count):
                days=weekdays(count)
                p=panel(days)
                tech=analyze_observed_panel(p, days)["indicators"]
                chart=build_observed_daily_chart(p, days)
                self.assertEqual(tech["daily_ma120"] is not None, count >= 120)
                self.assertEqual(tech["daily_ma240"] is not None, count >= 240)
                self.assertEqual(chart["latest_ma"]["120"],tech["daily_ma120"])
                self.assertEqual(chart["latest_ma"]["240"],tech["daily_ma240"])
                self.assertEqual(chart["rendered_days"],min(count,260))
                self.assertFalse(chart["eligible_for_v3_score"])
                self.assertFalse(chart["confirmed_buy"])
                self.assertFalse(chart["official_regular_session_verified"])
        last=build_observed_daily_chart(panel(weekdays(240)),weekdays(240))
        # Prices 100 through 339, 240-day mean 219.5.
        self.assertEqual(last["latest_ma"]["240"],219.5)
        self.assertEqual(last["latest_ma"]["120"],279.5)
        self.assertEqual(last["timeline"][-1]["ma"]["240"],219.5)

    def test_both_lines_in_same_local_daily_candlestick_svg(self):
        d=weekdays(270)
        chart=build_observed_daily_chart(panel(d),d)
        html=render_private_daily_chart_html(chart,stock_label="비공개 연구 샘플")
        for phrase in ("일봉 5일선","일봉 20일선","일봉 60일선",
                       "일봉 120일선","일봉 240일선","<svg","<rect",
                       "<polyline", "비공개 연구 샘플", "검증 전 연구용"):
            self.assertIn(phrase,html)
        for color in ("#815bd1","#d14970"):
            self.assertIn(color,html)
        self.assertIn('script-src &#39;none&#39;' .replace("&#39;","'"),html)
        self.assertNotIn("<script",html)
        self.assertNotIn("https://",html)
        self.assertNotIn("cdn.",html)

    def test_insufficient_history_prints_material_shortage_not_flat_zero(self):
        d=weekdays(40)
        chart=build_observed_daily_chart(panel(d),d)
        html=render_private_daily_chart_html(chart)
        self.assertIn("일봉 120일선 <b>자료 부족</b>",html)
        self.assertIn("일봉 240일선 <b>자료 부족</b>",html)
        self.assertIsNone(chart["latest_ma"]["120"])
        self.assertIsNone(chart["latest_ma"]["240"])

    def test_missing_day_breaks_ma_and_does_not_bridge_curve(self):
        d=weekdays(280)
        skipped=d[-7]
        c=build_observed_daily_chart(panel(d,missing={skipped}),d)
        self.assertEqual(c["latest_ma"]["5"], 377.0)
        self.assertIsNone(c["latest_ma"]["120"])
        self.assertIsNone(c["latest_ma"]["240"])
        self.assertEqual(c["timeline"][-7]["date"], skipped)
        self.assertIsNone(c["timeline"][-7]["candle"])
        self.assertIsNone(c["timeline"][-1]["ma"]["120"])

    def test_over35pct_unadjusted_discontinuity_splits_all_ma(self):
        d=weekdays(250)
        closes=[100.0]*140+[50.0]*110
        c=build_observed_daily_chart(panel(d,closes=closes),d)
        self.assertIsNotNone(c["latest_ma"]["60"])
        self.assertIsNone(c["latest_ma"]["120"])
        self.assertIsNone(c["latest_ma"]["240"])
        self.assertEqual(c["warning"],"large_unadjusted_price_jump_in_older_history")
        self.assertIsNone(c["timeline"][140]["ma"]["120"])
        html=render_private_daily_chart_html(c)
        self.assertIn("35% 이상 단절",html)

    def test_reject_future_or_duplicate_and_spoofed_source(self):
        days=weekdays(40)
        with self.assertRaisesRegex(PrivateDailyChartError,"invalid_daily_chart_window"):
            build_observed_daily_chart(panel(days),days+days[-1:])
        # Normal research analyst cannot insert an unverified newer date.
        extra=weekdays(41)
        with self.assertRaisesRegex(PrivateDailyChartError,"bar_outside_archive_calendar"):
            build_observed_daily_chart(panel(extra),days)
        spoof=panel(days)
        spoof["observed_bars"][-1]["regular_only_volume_verified"]=True
        with self.assertRaisesRegex(PrivateDailyChartError,"private_technical_preflight_failed"):
            build_observed_daily_chart(spoof,days)

    def test_local_file_only_explicit_consent_and_private_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"private"/"snapshots.sqlite3"
            today="2026-10-16"
            store_pair(
                database_path=db,asof=today,
                kospi_raw=raw(today,"005930"),kosdaq_raw=raw(today,"247540"),
                kospi_received_at_utc=today+"T07:40:00Z",
                kosdaq_received_at_utc=today+"T07:40:01Z")
            chart=build_observed_daily_chart(panel([today]),[today])
            html=render_private_daily_chart_html(chart,stock_label="<b>fake")
            self.assertIn("&lt;b&gt;fake",html)
            inside=db.parent/"my-secret-chart.html"
            with self.assertRaisesRegex(PrivateDailyChartError,"explicit_local_chart_retention_ack_required"):
                save_private_chart_html(output_path=inside,private_db=db,html=html)
            self.assertFalse(inside.exists())
            other=Path(temp)/"public.html"
            with self.assertRaisesRegex(PrivateDailyChartError,"chart_must_stay_beside_private_database"):
                save_private_chart_html(output_path=other,private_db=db,html=html,
                                        acknowledge=True)
            self.assertFalse(other.exists())
            saved=save_private_chart_html(output_path=inside,private_db=db,html=html,
                                          acknowledge=True)
            self.assertFalse(saved["published"])
            self.assertEqual(inside.read_text(encoding="utf-8"),html)
            if os.name=="posix":
                self.assertEqual(stat.S_IMODE(inside.stat().st_mode),0o600)
            with self.assertRaisesRegex(PrivateDailyChartError,"private_chart_cannot_be_created"):
                save_private_chart_html(output_path=inside,private_db=db,html=html,
                                        acknowledge=True)

    def test_cli_aggregate_only_without_chart_export_or_live_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"private"/"snapshot.sqlite3"
            output=StringIO()
            with redirect_stdout(output):
                result=main([
                    "--start","2026-10-15","--end","2026-10-16",
                    "--calendar-source","weekdays","--private-db",str(db),
                    "--market","KOSPI","--code","005930"])
            self.assertEqual(result,0)
            self.assertFalse(db.exists())
            printed=output.getvalue()
            for secret in ("005930",str(db),"TDD_OPNPRC",'"timeline"','"candle"'):
                self.assertNotIn(secret,printed)
            self.assertIn('"daily_ma120_available": false',printed)
            self.assertIn('"daily_ma240_available": false',printed)
            self.assertIn('"actual_api_calls": 0',printed)


if __name__=="__main__":
    unittest.main()
