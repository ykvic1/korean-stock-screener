"""No-network, no-fake-score tests for exact-context DART CAPEX label pilot."""
from datetime import date
from unittest import TestCase
from unittest.mock import patch

import v3_dart_capex_taxonomy_probe as p
from v3_dart_cashflow_enrichment import normalize_year


YEAR = 2025
ASOF = "2026-10-23"
STOCK = "000020"
CORP = "00123456"
RECEIPT = "20260331001234"


def row(account_id, name, amount="0", **overrides):
    d = {"corp_code": CORP, "stock_code": STOCK, "bsns_year": str(YEAR),
         "reprt_code": "11011", "fs_div": "CFS", "sj_div": "CF",
         "account_detail": "-", "currency": "KRW",
         "rcept_no": RECEIPT, "account_id": account_id,
         "account_nm": name, "thstrm_amount": amount}
    d.update(overrides)
    return d


class Response:
    def __init__(self, *, payload=None, content=b"fixture"):
        self.payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, listed):
        self.listed = listed
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.listed[len(self.calls) - 1]


class DARTCapexTaxonomyPilotTests(TestCase):
    def test_missing_class_is_exclusive_and_never_coerces_none_to_zero(self):
        both = {"ppe_capex_krw": None, "intangible_capex_krw": None}
        self.assertEqual(p.missing_class(both), "missing_both_capex")
        self.assertEqual(p.missing_class({**both, "ppe_capex_krw": 0}),
                         "missing_intangible_only")
        self.assertEqual(p.missing_class({**both, "intangible_capex_krw": 0}),
                         "missing_ppe_only")
        self.assertIsNone(p.missing_class({"ppe_capex_krw": 0,
                                           "intangible_capex_krw": 0}))

    def test_candidate_accepts_only_matched_receipt_scope_currency_and_year(self):
        good = row("-표준계정코드 미사용-", "무형자산의 취득", "0")
        args = dict(year=YEAR, scope="CFS", corp=CORP, stock=STOCK,
                    receipt=RECEIPT, cutoff=date(2026, 10, 23))
        self.assertEqual(p.candidate_fields(good, **args), ("intangible",))
        self.assertEqual(p.candidate_fields(row("x", "유형자산의 취득"), **args),
                         ("ppe",))
        for bad in (row("x", "무형자산의 처분"),
                    row("x", "무형자산의 취득", currency="USD"),
                    row("x", "무형자산의 취득", fs_div="OFS"),
                    row("x", "무형자산의 취득", bsns_year="2024"),
                    row("x", "무형자산의 취득", rcept_no="20251231000000"),
                    row("x", "무형자산의 취득", account_detail="entity"),
                    row("x", "무형자산의 취득", sj_div="IS"),
                    row("x", "무형자산의 취득", corp_code="00000000")):
            self.assertEqual(p.candidate_fields(bad, **args), ())
        self.assertEqual(p.candidate_fields(good, **{**args,
                          "cutoff": date(2025, 12, 31)}), ())

    def test_zero_is_observed_numeric_candidate_only_not_zero_imputation(self):
        info, hits = p.summarize_candidates([
            row("-표준계정코드 미사용-", "무형자산 취득", "0"),
            row("custom", "유형자산 매각", "999"),
        ], stock=STOCK, corp=CORP, year=YEAR, scope="CFS",
           receipt=RECEIPT, cutoff=date(2026, 10, 23),
           missing={"ppe": True, "intangible": True})
        self.assertEqual(info["intangible"]["rows"], 1)
        self.assertEqual(info["intangible"]["with_numeric_amount"], 1)
        self.assertEqual(info["intangible"]["nonstandard_rows"], 1)
        self.assertEqual(info["ppe"]["rows"], 0)
        self.assertEqual(hits, {"ppe": False, "intangible": True})

    def test_bounded_queue_balances_missing_kinds_and_exchanges(self):
        records = {
            "000020": {"scope": "CFS", "years": {"2025": {
                "ppe_capex_krw": None, "intangible_capex_krw": None}}},
            "000030": {"scope": "OFS", "years": {"2025": {
                "ppe_capex_krw": 5, "intangible_capex_krw": None}}},
            "000040": {"scope": "CFS", "years": {"2024": {
                "ppe_capex_krw": None, "intangible_capex_krw": 0}}},
        }
        state = {"records": records}
        markets = {"000020": {"market": "KOSPI"},
                   "000030": {"market": "KOSDAQ"},
                   "000040": {"market": "KOSPI"}}
        with patch.object(p, "verify_state"), patch.object(
                p, "asof_members", return_value=(ASOF, markets)):
            selected = p.targets(state, {}, {}, {}, limit=3)
            self.assertEqual(len(selected), 3)
            self.assertEqual({item[0] for item in selected},
                             set(p.CLASSES))
            self.assertRaises(p.TaxonomyPilotError, p.targets,
                              state, {}, {}, {}, limit=9)

    def test_bounded_live_probe_reuses_unchanged_official_source_only(self):
        known = row(
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "영업활동현금흐름", "250")
        ppe = row(
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "유형자산의 취득", "50")
        other = row("-표준계정코드 미사용-", "무형자산의 취득", "0")
        rows = [known, ppe, other]
        old = normalize_year([known, ppe], code=STOCK, corp=CORP, year=YEAR,
                             scope="CFS", asof=ASOF, major_receipt=RECEIPT)
        self.assertEqual(old["intangible_capex_krw"], None)
        self.assertEqual(old["ppe_capex_krw"], 50)
        state = {"asof": ASOF, "records": {STOCK: {"scope": "CFS",
                 "years": {str(YEAR): old}}}}
        major = {"records": {STOCK: {"years": {str(YEAR):
                 {"CFS": {"receipt_id": RECEIPT}}}}}}
        session = Session([
            Response(content=b"corporations"),
            Response(payload={"status": "000", "list": rows}),
        ])
        selected = [("missing_intangible_only", "KOSPI",
                     STOCK, YEAR, "CFS", old)]
        with patch.object(p, "targets", return_value=selected), patch.object(
                p, "asof_members", return_value=(ASOF,
                {STOCK: {"market": "KOSPI"}})), patch.object(
                p, "_mapping", return_value={STOCK: CORP}):
            report = p.probe(state, {}, {}, major, key="x" * 21,
                             session=session)
        self.assertEqual(report["provider_calls"], 2)
        self.assertEqual(report["completed_year_calls"], 1)
        self.assertEqual(report["candidate_numeric_rows"]["intangible"], 1)
        self.assertEqual(report["nonstandard_label_rows"]["intangible"], 1)
        self.assertTrue(report["no_new_fcf_scores_or_zero_imputation"])
        self.assertNotIn(STOCK, str(report))
        self.assertNotIn(RECEIPT, str(report))
        self.assertEqual(len(session.calls), 2)

    def test_provider_failures_never_print_source_urls_or_secrets(self):
        old = {"ppe_capex_krw": None, "intangible_capex_krw": None}
        selected = [("missing_both_capex", "KOSPI",
                     STOCK, YEAR, "CFS", old)]
        class Exploding:
            def get(self, url, **kwargs):
                raise RuntimeError("secret-in-query-string")
        with patch.object(p, "targets", return_value=selected), patch.object(
                p, "asof_members", return_value=(ASOF,
                {STOCK: {"market": "KOSPI"}})):
            with self.assertRaises(p.TaxonomyPilotError) as caught:
                p.probe({"asof": ASOF}, {}, {}, {}, key="a" * 21,
                        session=Exploding())
        self.assertEqual(str(caught.exception),
                         "official_mapping_unavailable")
        self.assertNotIn("secret-in-query-string", str(caught.exception))
