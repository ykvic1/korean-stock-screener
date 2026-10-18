"""Mobile local report offline safety and full 64-stock demo tests."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

from v3_mobile_report import (
    MobileReportError, render_mobile_report, save_mobile_report,
)
from v3_offline_workbench import demo_bundle, run_workbench


class MobileReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = run_workbench(demo_bundle())

    def test_static_responsive_research_only_six_factor_report(self):
        html = render_mobile_report(self.sample)
        self.assertIn('name="viewport"', html)
        self.assertIn("SYNTHETIC_DEMO", html)
        self.assertIn("기관·외국인 매집", html)
        self.assertIn("재무·현금흐름", html)
        self.assertIn("기업가치", html)
        self.assertIn("업종 사이클", html)
        self.assertIn("중복 지표 경고", html)
        self.assertIn("연구 대상 64개", html)
        self.assertIn("확정순위 없음", html)
        self.assertIn("실전 매수·매도 추천이 아닙니다", html)
        self.assertEqual(html.count("<article><details>"), 64)
        self.assertNotIn("<script", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("institution_total_net_shares", html)
        self.assertNotIn("operating_profit_krw", html)

    def test_loss_first_gate_is_visible_above_score_and_missing_gate_blocks(self):
        html = render_mobile_report(self.sample)
        self.assertEqual(html.count("손실위험 우선 판정:"), 64)
        self.assertLess(html.index("손실위험 우선 판정:"),
                        html.index("잠정 연구 합계:"))
        sample = deepcopy(self.sample)
        sample["stocks_in_code_order_not_ranked"][0][
            "downside_risk_screen"]["status_research_only"] = "structural_exclusion"
        sample["stocks_in_code_order_not_ranked"][0][
            "downside_risk_screen"]["exclusion_reasons"] = [
                "latest_confirmed_swing_low_broken"]
        view = render_mobile_report(sample)
        self.assertIn("고득점이라도 제외", view)
        sample = deepcopy(self.sample)
        sample["stocks_in_code_order_not_ranked"][0].pop("downside_risk_screen")
        with self.assertRaisesRegex(MobileReportError, "risk_first_research_gate_missing"):
            render_mobile_report(sample)

    def test_private_html_write_atomic_permissions_and_no_raw_market_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "v3-mobile.html"
            audit = save_mobile_report(self.sample, output_path=path)
            self.assertEqual(audit["status"], "private_research_html_created")
            self.assertFalse(audit["confirmed_buy"])
            self.assertFalse(audit["public_pages_changed"])
            html = path.read_text(encoding="utf-8")
            self.assertIn("자료 100%", html)
            self.assertEqual(html.count("<article><details>"), 64)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            first = html
            save_mobile_report(self.sample, output_path=path)
            self.assertEqual(path.read_text(encoding="utf-8"), first)
            self.assertEqual(list(Path(folder).glob(".v3-report-*")), [])

    def test_script_in_untrusted_displayed_text_is_escaped(self):
        r = deepcopy(self.sample)
        r["stocks_in_code_order_not_ranked"][0]["code"] = "<img src=x onerror=alert(1)>"
        html = render_mobile_report(r)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn("onerror=alert(1)>", html)

    def test_unverified_row_never_labelled_observed_zero_score(self):
        r = deepcopy(self.sample)
        row = r["stocks_in_code_order_not_ranked"][0]
        row["pillar_research_candidates"]["financial"] = None
        row["candidate_full_total_research_only"] = None
        row["candidate_coverage_pct"] = 80
        html = render_mobile_report(r)
        self.assertIn("미확인", html)
        self.assertIn("자료 80%", html)
        self.assertFalse("확정 매수" in html)

    def test_confirmed_score_or_buy_claim_blocks_rendering(self):
        mutations = (
            lambda d: d.update(confirmed_buy=True),
            lambda d: d.update(v3_release_ready=True),
            lambda d: d.update(confirmed_stock_ranks=[{"code": "000001"}]),
            lambda d: d.update(recommended_positions=[]),
            lambda d: d["stocks_in_code_order_not_ranked"][0].update(
                confirmed_score=75),
            lambda d: d["stocks_in_code_order_not_ranked"][0].update(
                confirmed_buy=True),
            lambda d: d["stocks_in_code_order_not_ranked"][0][
                "pillar_research_candidates"].update(ias=26),
        )
        for i, mutate in enumerate(mutations):
            r = deepcopy(self.sample)
            mutate(r)
            with self.subTest(i=i), self.assertRaises(MobileReportError):
                render_mobile_report(r)

    def test_hypothetical_gap_loss_display_cannot_grant_trade_permission(self):
        batch = demo_bundle()
        batch["hypothetical_portfolio_plans"] = [{
            "code": "000001", "entry_price": 100, "stop_price": 92,
            "stress_gap_below_stop_pct": 5,
            "planned_position_pct": 20, "reference_target_price": 120,
        }]
        batch["hypothetical_nav_risk_budget_pct"] = 1
        report = run_workbench(batch)
        text = render_mobile_report(report)
        self.assertIn("2.5%", text)
        self.assertIn("exceeds_assumed_budget", text)
        self.assertIn("실제 하락폭은 더 클 수 있습니다", text)
        self.assertFalse(report["hypothetical_portfolio_risk_only"]["trade_approval"])

    def test_public_repository_and_open_permissions_forbidden(self):
        public_path = Path(__file__).resolve().parents[1] / "docs/v3-report-public.html"
        with self.assertRaisesRegex(MobileReportError, "public_repo"):
            save_mobile_report(self.sample, output_path=public_path)
        self.assertFalse(public_path.exists())
        with tempfile.TemporaryDirectory() as folder:
            if os.name == "posix":
                os.chmod(folder, 0o755)
                with self.assertRaisesRegex(MobileReportError, "directory_open"):
                    save_mobile_report(
                        self.sample, output_path=Path(folder) / "private.html")

    def test_cli_creates_64_synthetic_preview_without_publishing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preview.html"
            command = [sys.executable, "-m", "v3_mobile_report", "--demo",
                       "--output", str(path)]
            r = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            result = json.loads(r.stdout)
            self.assertEqual(result["stock_count"], 64)
            self.assertEqual(result["status"], "private_research_html_created")
            self.assertFalse(result["public_pages_changed"])
            self.assertTrue(path.exists())
            self.assertIn("SYNTHETIC_DEMO", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
