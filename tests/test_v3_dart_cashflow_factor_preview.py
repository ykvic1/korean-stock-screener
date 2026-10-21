"""No-network independent FCF/assets and FCF-yield research preview tests."""
import copy
import json
from pathlib import Path
import unittest

from test_v3_dart_cashflow_enrichment import live_inputs, FakeSession
import v3_dart_cashflow_enrichment as cf
import v3_dart_cashflow_factor_preview as factor


def five_complete():
    market,catalog,bulk=live_inputs()
    for row in bulk["records"].values():
        for yr in ("2023","2024","2025"):
            row["years"][yr]["CFS"]["values"]["assets"]=10_000_000_000
    c,_=cf.collect(market,catalog,bulk,None,
        key="synthetic-offline-token-no-provider",session=FakeSession(),
        issuer_limit=5)
    return market,catalog,bulk,c


def fifty_source_matched():
    base=Path(__file__).resolve().parent.parent/"docs"
    def load(name):
        return json.loads((base/name).read_text(encoding="utf8"))
    market=load("data.json")
    catalog=load("v22-market-catalog.json")
    major=load("v3-dart-bulk-history.json")
    # Synthesize CFO and both investment outflows ONLY for offline tests,
    # tying all cashflow synthetic values to existing real filing IDs.
    codes=[]
    scope_by_code={}
    for code,row in sorted(major["records"].items()):
        if code not in {v["code"] for v in catalog["rows"] if v["market"]=="KOSPI"}:
            continue
        scope=cf.matched_scope(row)
        if scope is None:
            continue
        years=row["years"]
        if all(type(years[str(y)][scope]["values"].get("assets")) is int and
               years[str(y)][scope]["values"]["assets"]>0
               for y in cf.YEARS):
            codes.append(code)
            scope_by_code[code]=scope
            if len(codes)==35:
                break
    if len(codes)!=35:
        raise AssertionError("need at least 35 full fiscal asset issuer fixtures")
    records={}
    for index,code in enumerate(codes):
        scope=scope_by_code[code]
        rows={}
        for year in cf.YEARS:
            receipt=major["records"][code]["years"][str(year)][scope]["receipt_id"]
            cfo=1_000_000_000+index*1_000_000
            ppe=100_000_000
            inta=20_000_000
            rows[str(year)]={
                "year":year,"scope":scope,"annual_receipt_id":receipt,
                "major_annual_receipt_matched":True,
                "operating_cashflow_krw":cfo,
                "ppe_capex_krw":ppe,"intangible_capex_krw":inta,
                "total_capex_krw":ppe+inta,
                "free_cashflow_krw":cfo-ppe-inta,
                "raw_source_account_rows_published":False,
                "historical_PIT_certified":False,
            }
        records[code]={"scope":scope,"years":rows}
    snapshot=cf.summary(major["asof"],{v["code"]:v for v in catalog["rows"]},
                        records,cf.fingerprint(major))
    return market,catalog,major,snapshot


class FCFPreviewTests(unittest.TestCase):
    def test_no_peer_fabrication_even_with_observed_fcf(self):
        market,catalog,bulk,cash=five_complete()
        report=factor.score_preview(market,catalog,bulk,cash,minimum_peers=8)
        self.assertEqual(report["real_fcf_assets_three_year_raw_issuer_count"],5)
        self.assertEqual(report["financial_fcf4_research_candidate_issuer_count"],0)
        self.assertEqual(report["valuation_fcf5_research_candidate_issuer_count"],0)
        self.assertTrue(report["source_same_filing_scope_receipt_checked"])
        self.assertIsNone(report["six_pillar_confirmed_100pt"])
        self.assertFalse(report["joined_to_main_v3_ranking"])
        self.assertTrue(all(x["financial_fcf_assets_candidate_points"] is None
                            for x in report["rows"]))

    def test_source_bounded_35_peer_research_score_does_not_promote_buy(self):
        market,catalog,bulk,cash=fifty_source_matched()
        preview=factor.score_preview(market,catalog,bulk,cash)
        self.assertEqual(preview["real_fcf_assets_three_year_raw_issuer_count"],35)
        self.assertEqual(preview["financial_fcf4_research_candidate_issuer_count"],35)
        self.assertEqual(preview["source_same_filing_scope_receipt_checked"],True)
        self.assertTrue(all(0<=r["financial_fcf_assets_candidate_points"]<=4
                            for r in preview["rows"]))
        self.assertEqual(preview["valuation_fcf_weight_available_max"],5)
        self.assertFalse(preview["valuation15_complete"])
        self.assertFalse(preview["buy_recommendations_authorized"])
        changed=copy.deepcopy(cash)
        first=next(iter(changed["records"]))
        changed["records"][first]["years"]["2024"]["annual_receipt_id"]="20270101000001"
        # Past/future revised statements may remain separately recorded in the
        # cashflow source, but cannot be blended with the original major BS.
        changed["records"][first]["years"]["2024"]["major_annual_receipt_matched"]=False
        older=factor.score_preview(market,catalog,bulk,changed)
        self.assertEqual(older["real_fcf_assets_three_year_raw_issuer_count"],34)

    def test_cashflow_changed_formula_hash_or_source_tampering_fails_closed(self):
        market,catalog,bulk,cash=five_complete()
        changed=copy.deepcopy(cash)
        code=next(iter(changed["records"]))
        changed["records"][code]["years"]["2025"]["free_cashflow_krw"]+=1
        with self.assertRaisesRegex(cf.CashflowError,"untrusted_cashflow_fcf_formula"):
            factor.score_preview(market,catalog,bulk,changed,minimum_peers=8)
        wrong=copy.deepcopy(bulk)
        wrong["listed_total"]+=1
        with self.assertRaises(cf.CashflowError):
            factor.score_preview(market,catalog,wrong,cash,minimum_peers=8)
        with self.assertRaisesRegex(factor.FCFPreviewError,"invalid_fcf_peer_minimum"):
            factor.score_preview(market,catalog,bulk,cash,minimum_peers=0)


if __name__=="__main__":
    unittest.main()
