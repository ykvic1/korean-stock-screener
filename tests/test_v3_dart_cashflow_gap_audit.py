"""No-network regression for aggregate-only DART CAPEX source missingness."""
import copy
import json
import unittest

from test_v3_dart_cashflow_enrichment import live_inputs
import v3_dart_cashflow_enrichment as cash
import v3_dart_cashflow_gap_audit as gap


class GapAuditTests(unittest.TestCase):
    def _fixture(self):
        market, catalog, major = live_inputs()
        asof, members = cash.asof_members(market, catalog, major)
        candidates = sorted(members)[:3]
        observations = [
            (None, None, None, False),
            (None, 10, 20, False),
            (100, None, None, False),
            (100, None, 20, False),
            (100, 10, None, False),
            (100, 10, 20, False),
            (100, 10, 20, True),
        ]
        records = {}
        for i, (cfo, ppe, intangible, matched) in enumerate(observations):
            code = candidates[i // 3]
            year = 2023 + i % 3
            expected_receipt = major["records"][code]["years"][str(year)][
                "CFS"]["receipt_id"]
            receipt = (expected_receipt if matched
                       else expected_receipt[:-1] + "2")
            capex = ppe + intangible if ppe is not None and intangible is not None else None
            fcf = cfo - capex if cfo is not None and capex is not None else None
            row = {
                "year": year, "scope": "CFS",
                "annual_receipt_id": receipt,
                "major_annual_receipt_matched": matched,
                "operating_cashflow_krw": cfo,
                "ppe_capex_krw": ppe,
                "intangible_capex_krw": intangible,
                "total_capex_krw": capex,
                "free_cashflow_krw": fcf,
                "raw_source_account_rows_published": False,
                "historical_PIT_certified": False,
            }
            records.setdefault(code, {"scope": "CFS", "years": {}})["years"][str(year)] = row
        state = cash.summary(asof, members, records, cash.fingerprint(major))
        return market, catalog, major, state, candidates

    def test_missingness_partition_uses_only_attempted_real_years(self):
        market, catalog, major, state, candidates = self._fixture()
        result = gap.audit_cashflow_gaps(state, market, catalog, major)
        self.assertEqual(result["attempted_company_years"], 7)
        self.assertEqual(result["missing_fcf_company_years"], 5)
        self.assertEqual(result["observed_fcf_company_years"], 2)
        self.assertEqual(list(result["categories"].values()), [1] * 7)
        self.assertEqual(result["issuers_with_saved_cashflow_years"], 3)
        self.assertEqual(result["provider_api_calls"], 0)
        self.assertFalse(result["trading_score_promoted"])
        self.assertFalse(result["historical_pit_verified"])
        self.assertTrue(result["missing_ifrs_account_not_interpreted_as_zero"])
        report = json.dumps(result, sort_keys=True)
        for code in candidates:
            self.assertNotIn(code, report)
        self.assertNotIn("annual_receipt_id", report)
        self.assertNotIn("free_cashflow_krw", report)

    def test_three_year_issuer_depth_separates_unfetched_from_missing_accounts(self):
        market, catalog, major, state, _ = self._fixture()
        result = gap.audit_cashflow_gaps(state, market, catalog, major)
        depth = result["three_year_issuer_depth"]
        self.assertEqual(depth["no_saved_cashflow_years"],
                         result["listed_issuer_count"] - 3)
        self.assertEqual(depth["saved_incomplete_years"], 1)
        self.assertEqual(depth["three_years_attempted_missing_accounts"], 2)
        self.assertEqual(depth["three_years_with_fcf_but_unmatched_receipts"], 0)
        self.assertEqual(depth["three_year_source_receipt_complete"], 0)
        self.assertEqual(sum(depth.values()), result["listed_issuer_count"])
        self.assertGreaterEqual(result["source_eligible_unfetched_issuer_count"], 0)
        self.assertLessEqual(result["source_eligible_unfetched_company_years"],
                             result["listed_issuer_count"] * 3 -
                             result["attempted_company_years"])
        self.assertEqual(result["provider_api_calls"], 0)
        self.assertTrue(result["unattempted_years_not_interpreted_as_zero"])

    def test_complete_and_receipt_unmatched_cohorts_never_mix(self):
        market, catalog, major, state, candidates = self._fixture()
        altered = copy.deepcopy(state["records"])
        for code, matched in ((candidates[0], True), (candidates[1], False)):
            for year, row in altered[code]["years"].items():
                row["operating_cashflow_krw"] = 100
                row["ppe_capex_krw"] = 10
                row["intangible_capex_krw"] = 20
                row["total_capex_krw"] = 30
                row["free_cashflow_krw"] = 70
                row["major_annual_receipt_matched"] = matched
                if matched:
                    row["annual_receipt_id"] = major["records"][code]["years"][
                        year]["CFS"]["receipt_id"]
        rebuilt = cash.summary(state["asof"], cash.asof_members(
            market, catalog, major)[1], altered, cash.fingerprint(major))
        result = gap.audit_cashflow_gaps(rebuilt, market, catalog, major)
        depth = result["three_year_issuer_depth"]
        self.assertEqual(depth["three_year_source_receipt_complete"], 1)
        self.assertEqual(depth["three_years_with_fcf_but_unmatched_receipts"], 1)
        self.assertEqual(depth["three_years_attempted_missing_accounts"], 0)
        self.assertEqual(result["observed_fcf_company_years"], 6)
        self.assertEqual(result["provider_api_calls"], 0)

    def test_zero_capex_is_observed_only_when_both_components_are_real(self):
        row = {"operating_cashflow_krw": 50,
               "ppe_capex_krw": 0, "intangible_capex_krw": 0,
               "major_annual_receipt_matched": True}
        self.assertEqual(gap._classify(row), "fcf_observed_receipt_matched")
        row["intangible_capex_krw"] = None
        self.assertEqual(gap._classify(row), "missing_intangible_only")

    def test_source_or_derived_counts_tampering_fails_closed(self):
        market, catalog, major, state, _ = self._fixture()
        broken = copy.deepcopy(state)
        broken["metrics"]["fcf_company_years"] += 1
        with self.assertRaises(gap.GapAuditError):
            gap.audit_cashflow_gaps(broken, market, catalog, major)
        broken = copy.deepcopy(state)
        first = next(iter(broken["records"].values()))
        year = next(iter(first["years"].values()))
        year["operating_cashflow_krw"] = 100
        with self.assertRaises(gap.GapAuditError):
            gap.audit_cashflow_gaps(broken, market, catalog, major)


if __name__ == "__main__":
    unittest.main()
