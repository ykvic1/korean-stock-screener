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
                    row("x", "유형자산 및 무형자산의 취득"),
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

    def test_eight_target_sample_balances_year_market_and_missing_kind(self):
        records, markets = {}, {}
        n = 0
        for kind in p.CLASSES:
            for year in (2023, 2024, 2025):
                for market in ("KOSPI", "KOSDAQ"):
                    n += 1
                    code = f"{n:06d}"
                    amounts = {
                        "missing_both_capex": (None, None),
                        "missing_intangible_only": (4, None),
                        "missing_ppe_only": (None, 3),
                    }[kind]
                    records[code] = {
                        "scope": "CFS" if market == "KOSPI" else "OFS",
                        "years": {str(year): {
                            "ppe_capex_krw": amounts[0],
                            "intangible_capex_krw": amounts[1]}}}
                    markets[code] = {"market": market}
        # Add another historical year for one issuer to ensure one company
        # never consumes more than one bounded official source request.
        first = sorted(records)[0]
        records[first]["years"]["2024"] = {
            "ppe_capex_krw": None, "intangible_capex_krw": None}
        state = {"records": records}
        with patch.object(p, "verify_state"), patch.object(
                p, "asof_members", return_value=(ASOF, markets)):
            chosen = p.targets(state, {}, {}, {}, limit=8)
        self.assertEqual(len(chosen), 8)
        self.assertEqual(len({x[2] for x in chosen}), 8)
        for position, allowed_gap in ((0, 1), (1, 1), (3, 1), (4, 1)):
            distribution = {}
            for selected in chosen:
                value = selected[position]
                distribution[value] = distribution.get(value, 0) + 1
            self.assertLessEqual(
                max(distribution.values()) - min(distribution.values()),
                allowed_gap)
        self.assertEqual({x[3] for x in chosen}, {2023, 2024, 2025})
        self.assertEqual({x[1] for x in chosen}, {"KOSPI", "KOSDAQ"})

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
        self.assertEqual(sum(report["sampled_by_fiscal_year"].values()), 1)
        self.assertEqual(sum(report["sampled_by_market"].values()), 1)
        self.assertEqual(sum(report["sampled_by_scope"].values()), 1)
        self.assertEqual(sum(report["sampled_by_missing_class"].values()), 1)
        self.assertEqual(report["candidate_numeric_rows"]["intangible"], 1)
        self.assertEqual(report["nonstandard_label_rows"]["intangible"], 1)
        self.assertEqual(report["saved_supported_id_gap_causes"]["intangible"]["no_supported_flat_id"], 1)
        self.assertEqual(report["candidate_numeric_account_id_families"]["intangible"]["nonstandard"], 1)
        self.assertTrue(report["no_new_fcf_scores_or_zero_imputation"])
        self.assertNotIn(STOCK, str(report))
        self.assertNotIn(RECEIPT, str(report))
        self.assertEqual(len(session.calls), 2)

    def test_existing_exact_ifrs_id_null_reason_remains_unscored(self):
        accepted = "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
        args = {"kind": "ppe", "stock": STOCK, "corp": CORP,
                "year": YEAR, "scope": "CFS", "receipt": RECEIPT,
                "cutoff": date(2026, 10, 23)}
        self.assertEqual(p.supported_gap_cause([], **args),
                         "no_supported_flat_id")
        self.assertEqual(p.supported_gap_cause(
            [row(accepted, "유형자산의 취득", "not_a_number")], **args),
            "invalid_or_missing_amount")
        self.assertEqual(p.supported_gap_cause(
            [row(accepted, "유형자산의 취득", "100"),
             row(accepted, "유형자산의 취득", "200")], **args),
            "conflicting_duplicate_amounts")
        self.assertEqual(p.supported_gap_cause(
            [row(accepted, "유형자산의 취득", "-100")], **args),
            "negative_purchase_sign_not_certified")
        with self.assertRaises(p.TaxonomyPilotError):
            p.supported_gap_cause(
                [row(accepted, "유형자산의 취득", "100")], **args)
        self.assertEqual(p.supported_gap_cause(
            [row(accepted, "유형자산의 취득", "100",
                 account_detail="dimensional-item")], **args),
            "no_supported_flat_id")

    def test_official_account_namespace_is_aggregate_only(self):
        self.assertEqual(p.candidate_id_family(
            "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
            "intangible"), "already_supported")
        self.assertEqual(p.candidate_id_family(
            "ifrs-full_UnreviewedAcquisitionCashflow", "intangible"),
            "ifrs_other")
        self.assertEqual(p.candidate_id_family(
            "dart_ExampleAcquisitionTag", "ppe"), "dart_other")
        self.assertEqual(p.candidate_id_family(
            "-표준계정코드 미사용-", "ppe"), "nonstandard")
        self.assertEqual(p.candidate_id_family(
            "company-specific-private-tag", "ppe"), "other_or_missing")
        self.assertEqual(p.candidate_id_family(
            None, "intangible"), "other_or_missing")

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

    def test_exact_official_taxonomy_crossmatch_is_evidence_not_fcf_score(self):
        from v3_dart_capex_official_taxonomy import SECTIONS
        official_id = "dart_PurchaseOfPropertyPlantAndEquipment"
        known = row("ifrs-full_CashFlowsFromUsedInOperatingActivities",
                    "영업활동현금흐름", "250")
        candidate = row(official_id, "유형자산의 취득", "70")
        negative = row("dart_PaymentForAcquisitionOfIntangibleAssets",
                       "무형자산의 취득", "-40")
        old = normalize_year([known], code=STOCK, corp=CORP, year=YEAR,
                             scope="CFS", asof=ASOF, major_receipt=RECEIPT)
        self.assertIsNone(old["free_cashflow_krw"])
        state = {"asof": ASOF, "records": {STOCK: {"scope": "CFS",
                 "years": {str(YEAR): old}}}}
        major = {"records": {STOCK: {"years": {str(YEAR):
                 {"CFS": {"receipt_id": RECEIPT}}}}}}
        standard_rows = [
            {"sj_div": section, "account_id": entry_id, "account_nm": label,
             "label_kor": label, "label_eng": "", "data_tp": "X"}
            for section in SECTIONS
            for entry_id, label in ((official_id, "유형자산의 취득"),
                                    ("dart_PaymentForAcquisitionOfIntangibleAssets",
                                     "무형자산의 취득"))
        ]
        responses = [Response(payload={"status": "000", "list": [
            item for item in standard_rows if item["sj_div"] == section]})
            for section in SECTIONS]
        responses += [Response(content=b"companies"),
                      Response(payload={"status": "000",
                                        "list": [known, candidate, negative]})]
        selected = [("missing_both_capex", "KOSPI",
                     STOCK, YEAR, "CFS", old)]
        with patch.object(p, "targets", return_value=selected), patch.object(
                p, "asof_members", return_value=(ASOF,
                {STOCK: {"market": "KOSPI"}})), patch.object(
                p, "_mapping", return_value={STOCK: CORP}):
            report = p.probe(state, {}, {}, major, key="x" * 40,
                             session=Session(responses), check_official_taxonomy=True)
        self.assertEqual(report["provider_calls"], 6)
        self.assertEqual(report["official_taxonomy_section_calls"], 4)
        self.assertEqual(report["completed_year_calls"], 1)
        self.assertEqual(report["official_taxonomy_novel_nonnegative_numeric_rows"]["ppe"], 1)
        self.assertEqual(report["official_taxonomy_novel_negative_numeric_rows"]["intangible"], 1)
        self.assertEqual(report["official_taxonomy_novel_matching_ids"]["ppe"], [official_id])
        self.assertEqual(report["official_taxonomy_novel_matching_ids"]["intangible"], [])
        self.assertTrue(report["no_new_fcf_scores_or_zero_imputation"])
        self.assertTrue(report["official_taxonomy_match_does_not_certify_sign_or_fcf"])
        self.assertEqual(report["official_component_structure"]["ppe"]["single_component_completeness_unproven"], 1)
        self.assertEqual(report["official_component_structure"]["intangible"]["negative_sign_unverified"], 1)
        self.assertTrue(report["component_evidence_never_assumed_complete_or_additive"])
        self.assertNotIn(STOCK, str(report))
        self.assertNotIn(CORP, str(report))
        self.assertNotIn(RECEIPT, str(report))


    def test_same_statement_supported_total_is_control_only_without_extra_calls(self):
        from v3_dart_capex_official_taxonomy import SECTIONS
        known = row(
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "영업활동현금흐름", "250")
        known_ppe = row(
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "유형자산의 취득", "50")
        novel_ppe = row("dart_PurchaseOfMachinery", "기계장치의 취득", "10")
        novel_intangible = row(
            "dart_PurchaseOfIntangibleAssetsOtherThanGoodwill",
            "무형자산의 취득", "30")
        rows = [known, known_ppe, novel_ppe, novel_intangible]
        old = normalize_year([known, known_ppe], code=STOCK, corp=CORP,
                             year=YEAR, scope="CFS", asof=ASOF,
                             major_receipt=RECEIPT)
        self.assertEqual(old["ppe_capex_krw"], 50)
        self.assertIsNone(old["intangible_capex_krw"])
        self.assertIsNone(old["free_cashflow_krw"])
        state = {"asof": ASOF, "records": {STOCK: {"scope": "CFS",
                 "years": {str(YEAR): old}}}}
        major = {"records": {STOCK: {"years": {str(YEAR):
                 {"CFS": {"receipt_id": RECEIPT}}}}}}
        section_responses = []
        for section in SECTIONS:
            entries = [
                {"sj_div": section, "account_id": candidate["account_id"],
                 "account_nm": candidate["account_nm"],
                 "label_kor": candidate["account_nm"],
                 "label_eng": "", "data_tp": "X"}
                for candidate in (novel_ppe, novel_intangible)
            ]
            section_responses.append(Response(
                payload={"status": "000", "list": entries}))
        session = Session(section_responses + [
            Response(content=b"companies"),
            Response(payload={"status": "000", "list": rows})])
        selected = [("missing_intangible_only", "KOSPI",
                     STOCK, YEAR, "CFS", old)]
        with patch.object(p, "targets", return_value=selected), patch.object(
                p, "asof_members", return_value=(ASOF,
                {STOCK: {"market": "KOSPI"}})), patch.object(
                p, "_mapping", return_value={STOCK: CORP}):
            report = p.probe(state, {}, {}, major, key="x" * 40,
                             session=session, check_official_taxonomy=True)
        self.assertEqual(report["provider_calls"], 6)
        self.assertEqual(report["supported_total_control_checked_years"],
                         {"ppe": 1, "intangible": 0})
        self.assertEqual(
            report["supported_total_control_evidence"]["ppe"][
                "single_novel_below_supported_total"], 1)
        self.assertEqual(
            report["official_component_structure"]["intangible"][
                "single_component_completeness_unproven"], 1)
        self.assertEqual(
            report["official_taxonomy_novel_issuer_years"]["intangible"], 1)
        self.assertTrue(report["supported_total_comparison_never_approves_id_or_score"])
        self.assertTrue(report["no_new_fcf_scores_or_zero_imputation"])
        self.assertNotIn(STOCK, str(report))
        self.assertNotIn(RECEIPT, str(report))
        self.assertEqual(len(session.calls), 6)
