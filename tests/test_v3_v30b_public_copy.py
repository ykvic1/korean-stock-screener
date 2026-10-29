"""Prevent obsolete six-pillar scoring claims in V3.0b public screens.

Source-only regression tests: no network, browser session or trading signal.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = {"technical": 25, "volume": 20, "momentum": 15,
           "financial": 20, "valuation": 20}
PUBLIC = (
    "docs/v3-beta.html", "docs/v3-ranking.js", "docs/v3-manual.html",
    "docs/v3-beta.js", "docs/v3-coverage-gate.js",
)


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class V30bPublicWordingTests(unittest.TestCase):
    def test_all_public_beta_views_reject_superseded_scored_six_pillars(self):
        for path in PUBLIC:
            with self.subTest(path=path):
                source = read(path)
                for stale in (
                    "IAS20·기술25·재무15·가치15·산업15·모멘텀10",
                    "정식 6영역", "확정 6영역",
                    "IAS·재무·산업 6영역",
                    "v3 공식 6영역",
                ):
                    self.assertNotIn(stale, source)
        self.assertIn("거래량·OBV 20점", read("docs/v3-beta.html"))
        self.assertIn("기술 25점", read("docs/v3-beta.html"))
        self.assertIn("재무 20점", read("docs/v3-beta.html"))
        self.assertIn("밸류에이션 20점", read("docs/v3-beta.html"))
        self.assertIn("모멘텀 15점", read("docs/v3-beta.html"))
        self.assertIn("산업은 점수에 포함하지 않습니다", read("docs/v3-beta.html"))
        self.assertIn("정식 투자점수는 미인증", read("docs/v3-ranking.js"))
        self.assertIn("산업은 정보용", read("docs/v3-manual.html"))

    def test_coverage_gate_only_checks_five_current_areas(self):
        source = read("docs/v3-coverage-gate.js")
        match = re.search(r"const areas=(\[[^;]+\]);", source)
        self.assertIsNotNone(match)
        keys = re.findall(r"\['([a-z]+)','[^']+'\]", match.group(1))
        self.assertEqual(set(keys), set(WEIGHTS))
        self.assertEqual(len(keys), 5)
        self.assertIn("Object.keys(d.area_budgets).length!==areas.length", source)
        self.assertIn("d.certified_100point_count!==0", source)
        self.assertIn("현 V3.0b 100점 배점에서 제외", source)
        self.assertNotIn("safeCount(counts.industry", source)
        live = json.loads(read("docs/v3-current-observed-all-listed-summary.json"))
        self.assertEqual(live["area_budgets"], WEIGHTS)
        self.assertEqual(set(live["observed_issuer_counts"]), set(keys))
        self.assertEqual(live["certified_100point_count"], 0)

    def test_existing_v22_beta_and_unverified_score_not_promoted(self):
        html = read("docs/v3-beta.html")
        ranking = read("docs/v3-ranking.js")
        manual = read("docs/v3-manual.html")
        self.assertIn("기존 v2.2의 8영역 관측 기반 점수", html)
        self.assertIn("이 화면의 기존 v2.2 점수를 v3.0b 점수로 환산하지 않습니다", html)
        self.assertIn("기존 v2.2 점수", ranking)
        self.assertIn("확정 매수 추천이 아니며", ranking)
        self.assertIn("기존 v2.2 공개 일별 분석값", manual)
        self.assertIn("산업은 정보용이며 100점 배점에 포함되지 않습니다", read("docs/v3-beta.js"))


if __name__ == "__main__":
    unittest.main()
