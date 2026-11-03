"""No-network strict actual-source 100-weight coverage opportunity tests."""
import copy
import json
from pathlib import Path
import unittest

from v3_near_100_observed_gap import generate, GapAuditError


ROOT = Path(__file__).resolve().parents[1] / "docs"


def saved_sources():
    def read(name):
        return json.loads((ROOT / name).read_text(encoding="utf8"))
    return (
        (ROOT / "v3-current-observed-all-listed.csv").read_text(encoding="utf8"),
        read("v3-current-observed-all-listed-summary.json"),
        read("v3-dart-bulk-provisional.json"),
        read("v3-dart-book-reference.json"),
        read("v3-dart-cashflow-issuer-evidence.json"),
    )


class NearHundredSourceGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = saved_sources()

    def test_actual_same_day_market_count_matches_published_scorecard(self):
        report = generate(*self.sources)
        published = self.sources[1]
        self.assertEqual(report["asof"], published["asof"])
        self.assertEqual(report["listed_total"], published["listed_total"])
        self.assertEqual(
            report["provisional_observed_100_weight_issuers"],
            published["provisional_complete_100point_observation_count"])
        self.assertFalse(report["official_trade_score_certified"])
        self.assertTrue(report["issuer_last_observed_scores_unchanged"])
        self.assertTrue(report["no_points_added"])
        self.assertTrue(report["missing_capex_is_not_zero"])
        for gap in (4,5,9,12):
            self.assertEqual(
                report["near_complete_issuer_counts"][str(gap)],
                published["available_weight_distribution"].get(str(100-gap),0))
        self.assertEqual(
            len(report["official_2025_owners_profit_missing_priority_codes"]),
            report["official_2025_owners_profit_missing_priority_count"])
        self.assertEqual(
            len(set(report["official_2025_owners_profit_missing_priority_codes"])),
            report["official_2025_owners_profit_missing_priority_count"])

    def test_forged_100_count_rejected_and_no_missing_to_zero_shortcut(self):
        inputs=list(self.sources)
        wrong=copy.deepcopy(inputs[1])
        wrong["provisional_complete_100point_observation_count"]+=1
        inputs[1]=wrong
        with self.assertRaisesRegex(GapAuditError,
                                    "reported_100point_count_not_reproduced"):
            generate(*inputs)
        inputs=list(self.sources)
        fake=copy.deepcopy(inputs[1])
        fake["certified_100point_count"]=1
        inputs[1]=fake
        with self.assertRaisesRegex(GapAuditError,"untrusted_source_claim"):
            generate(*inputs)
        inputs=list(self.sources)
        csv_text=inputs[0]
        # A single one-hundred-weight claim without a real factor source
        # must not pass the per-issuer weight and sum crosscheck.
        before=",미검증 연구용,"
        self.assertIn(before,csv_text)
        inputs[0]=csv_text.replace(before,",인증 완료,",1)
        # The current provisional source is not allowed to certify.
        with self.assertRaises(GapAuditError):
            generate(*inputs)

    def test_stale_financial_or_cashflow_evidence_cannot_issue_priority(self):
        for slot in (2,3,4):
            inputs=list(self.sources)
            changed=copy.deepcopy(inputs[slot])
            changed["asof"]="2026-10-01"
            inputs[slot]=changed
            with self.subTest(slot=slot), self.assertRaisesRegex(
                    GapAuditError,"untrusted_source_claim_or_asof"):
                generate(*inputs)

    def test_fiscal_company_duplicate_and_bad_weights_block_output(self):
        inputs=list(self.sources)
        tampered=copy.deepcopy(inputs[2])
        self.assertTrue(tampered["rows"])
        tampered["rows"].append(copy.deepcopy(tampered["rows"][0]))
        inputs[2]=tampered
        with self.assertRaisesRegex(GapAuditError, "duplicate_fiscal_issuer"):
            generate(*inputs)
        inputs=list(self.sources)
        rows=inputs[0].splitlines()
        header=rows[0].split(",")
        field=header.index("재무_산출배점")
        before=rows[1].split(",")
        before[field]="21"
        rows[1]=",".join(before)
        inputs[0]="\n".join(rows)+"\n"
        with self.assertRaisesRegex(GapAuditError, "invalid_observed_weight"):
            generate(*inputs)

    def test_identity_ledger_is_not_part_of_same_day_full_coverage(self):
        report=generate(*self.sources)
        # A prior factor can remain on the user-facing ranked issuer card;
        # it must NOT be silently counted as a source acquired on this day.
        self.assertNotIn("last_observed_factor_points",report)
        self.assertTrue(report["no_points_added"])
        self.assertFalse(report["official_trade_score_certified"])


if __name__ == "__main__":
    unittest.main()
