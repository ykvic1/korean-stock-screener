"""Offline source/identity regression for bounded nonstandard CAPEX name pilot."""
import json
import unittest
from pathlib import Path

import v3_dart_bulk_history as bulk
import v3_dart_cashflow_enrichment as cash
import v3_dart_capex_name_pilot as pilot
from test_v3_dart_cashflow_enrichment import live_inputs


class Response:
    def __init__(self,payload):
        self.payload=payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self,payload):
        self.payload=payload
        self.calls=0

    def get(self,url,params,timeout):
        self.calls+=1
        return Response(self.payload)


class CapexNamePilotTests(unittest.TestCase):
    def test_weekly_research_workflow_stays_readonly_and_bounded(self):
        workflow=(Path(__file__).resolve().parents[1]/
                  ".github/workflows/v3-dart-capex-name-pilot.yml").read_text()
        self.assertIn("cron: '47 3 * * 0'",workflow)
        self.assertIn("contents: read",workflow)
        self.assertNotIn("contents: write",workflow)
        self.assertIn("--limit 12 --cohort-index",workflow)
        self.assertIn("COHORT_INDEX",workflow)
        self.assertIn("no scores",workflow)

    def _fixture(self):
        market,catalog,major=live_inputs()
        asof,members=cash.asof_members(market,catalog,major)
        code=sorted(members)[0]
        receipt=major["records"][code]["years"]["2025"]["CFS"]["receipt_id"]
        observation={
            "year":2025,"scope":"CFS","annual_receipt_id":receipt,
            "major_annual_receipt_matched":True,
            "operating_cashflow_krw":100,
            "ppe_capex_krw":20,
            "intangible_capex_krw":None,
            "total_capex_krw":None,"free_cashflow_krw":None,
            "raw_source_account_rows_published":False,
            "historical_PIT_certified":False,
        }
        records={code:{"scope":"CFS","years":{"2025":observation}}}
        state=cash.summary(asof,members,records,cash.fingerprint(major))
        corp="00126380"
        cached=bulk.make_company_mapping_cache({code:corp},asof,members)
        row={
            "sj_div":"CF","account_id":pilot.NONSTANDARD,
            "account_nm":"무형자산의 취득","account_detail":"-",
            "corp_code":corp,"stock_code":code,
            "bsns_year":"2025","reprt_code":"11011","fs_div":"CFS",
            "rcept_no":receipt,"currency":"KRW","thstrm_amount":"1,200",
        }
        return market,catalog,major,state,cached,row

    def test_exact_nonstandard_name_pilot_is_aggregate_only(self):
        market,catalog,major,state,cached,row=self._fixture()
        session=Session({"status":"000","list":[row]})
        result=pilot.run_pilot(
            market,catalog,major,state,mode="live",key="x"*40,
            session=session,corp_cache=cached)
        self.assertEqual(result["unique_exact_name_candidate"],1)
        self.assertEqual(result["provider_source_calls"],1)
        self.assertEqual(result["provider_corp_master_calls"],0)
        self.assertEqual(session.calls,1)
        self.assertTrue(result["no_derived_fcf_or_investment_score"])
        self.assertTrue(result["missing_amount_never_interpreted_as_zero"])
        output=json.dumps(result,ensure_ascii=False)
        self.assertNotIn(row["stock_code"],output)
        self.assertNotIn(row["rcept_no"],output)
        self.assertNotIn(row["account_nm"],output)
        self.assertNotIn(row["thstrm_amount"],output)

    def test_name_variants_and_nonstandard_requirement(self):
        _,_,_,_,_,row=self._fixture()
        args=dict(code=row["stock_code"],corp=row["corp_code"],year=2025,
                  scope="CFS",receipt=row["rcept_no"],kind="intangible")
        self.assertEqual(pilot.candidate_status([row],**args),
                         "unique_exact_name_candidate")
        row["account_nm"]="무형자산 취득"
        row["thstrm_amount"]="0"
        self.assertEqual(pilot.candidate_status([row],**args),
                         "unique_exact_name_candidate")
        row["account_nm"]="무형자산의 취득 및 처분"
        self.assertEqual(pilot.candidate_status([row],**args),
                         "no_exact_name_candidate")
        row["account_nm"]="무형자산의 취득"
        row["account_id"]="ifrs-full_SomeOtherExpense"
        self.assertEqual(pilot.candidate_status([row],**args),
                         "no_exact_name_candidate")

    def test_ambiguous_or_mismatched_identity_not_accepted(self):
        _,_,_,_,_,row=self._fixture()
        args=dict(code=row["stock_code"],corp=row["corp_code"],year=2025,
                  scope="CFS",receipt=row["rcept_no"],kind="intangible")
        self.assertEqual(pilot.candidate_status([row,row],**args),
                         "ambiguous_multiple_candidates")
        for changed in ("rcept_no","fs_div","currency","corp_code"):
            modified=dict(row)
            modified[changed]="wrong"
            with self.assertRaises(pilot.PilotError):
                pilot.candidate_status([modified],**args)
        invalid=dict(row,thstrm_amount="-50")
        with self.assertRaises(pilot.PilotError):
            pilot.candidate_status([invalid],**args)

    def test_bounded_stratified_sample_covers_year_scope_and_component(self):
        state={"records":{}}
        major={"records":{}}
        index=0
        for year in reversed(cash.YEARS):
            for scope in ("CFS","OFS"):
                for kind in ("ppe","intangible"):
                    index+=1
                    code=f"{index:06d}"
                    receipt=f"receipt-{index}"
                    observation={
                        "major_annual_receipt_matched":True,
                        "operating_cashflow_krw":100,
                        "ppe_capex_krw":None if kind=="ppe" else 10,
                        "intangible_capex_krw":None if kind=="intangible" else 5,
                        "annual_receipt_id":receipt,
                    }
                    state["records"][code]={
                        "scope":scope,"years":{str(year):observation}}
                    major["records"][code]={
                        "years":{str(year):{scope:{"receipt_id":receipt}}}}
        chosen=pilot.select_candidates(state,major)
        self.assertEqual(len(chosen),pilot.MAX_PROBES)
        self.assertEqual(len({item[0] for item in chosen}),pilot.MAX_PROBES)
        self.assertEqual({y:sum(item[1]==y for item in chosen)
                          for y in cash.YEARS},
                         {y:4 for y in cash.YEARS})
        self.assertEqual({scope:sum(item[2]==scope for item in chosen)
                          for scope in ("CFS","OFS")},
                         {"CFS":6,"OFS":6})
        self.assertEqual({kind:sum(item[4]==kind for item in chosen)
                          for kind in ("ppe","intangible")},
                         {"ppe":6,"intangible":6})
        self.assertEqual(len(pilot.select_candidates(state,major,limit=5)),5)
        # One issuer's old-year row never consumes two provider requests.
        first=sorted(state["records"])[0]
        state["records"][first]["years"]["2024"]={
            "major_annual_receipt_matched":True,
            "operating_cashflow_krw":100,
            "ppe_capex_krw":None,"intangible_capex_krw":10,
            "annual_receipt_id":"old-receipt",
        }
        major["records"][first]["years"]["2024"]={
            "CFS":{"receipt_id":"old-receipt"}}
        chosen=pilot.select_candidates(state,major)
        self.assertEqual(len(chosen),len({row[0] for row in chosen}))
        # A changed official annual receipt is never sampled silently.
        major["records"][first]["years"]["2024"]["CFS"]["receipt_id"]="revision"
        with self.assertRaises(pilot.PilotError):
            pilot.select_candidates(state,major)

    def test_weekly_cohorts_rotate_distinct_official_receipt_candidates(self):
        state={"records":{}}
        major={"records":{}}
        index=0
        for year in reversed(cash.YEARS):
            for scope in ("CFS","OFS"):
                for kind in ("ppe","intangible"):
                    for _ in range(3):
                        index+=1
                        code=f"{index:06d}"
                        receipt=f"synthetic-receipt-{index}"
                        state["records"][code]={
                            "scope":scope,"years":{str(year):{
                                "major_annual_receipt_matched":True,
                                "operating_cashflow_krw":100,
                                "ppe_capex_krw":None if kind=="ppe" else 10,
                                "intangible_capex_krw":None if kind=="intangible" else 5,
                                "annual_receipt_id":receipt,
                            }}}
                        major["records"][code]={
                            "years":{str(year):{scope:{"receipt_id":receipt}}}}
        weeks=[pilot.select_candidates(state,major,cohort_index=i)
               for i in range(4)]
        self.assertTrue(all(len(w)==pilot.MAX_PROBES for w in weeks))
        for i in range(3):
            self.assertEqual(len({row[0] for row in weeks[i]}),pilot.MAX_PROBES)
        self.assertTrue(all(not ({row[0] for row in weeks[i]} &
                                 {row[0] for row in weeks[j]})
                            for i in range(3) for j in range(i+1,3)))
        self.assertEqual(weeks[0],weeks[3])
        self.assertEqual(weeks[1],pilot.select_candidates(state,major,cohort_index=1))
        for invalid in (-1,1000001,True,1.2,"1"):
            with self.subTest(invalid=invalid),self.assertRaises(pilot.PilotError):
                pilot.select_candidates(state,major,cohort_index=invalid)

    def test_dry_run_never_uses_credentials_or_provider(self):
        market,catalog,major,state,_,_=self._fixture()
        report=pilot.run_pilot(market,catalog,major,state,mode="dry-run")
        self.assertEqual(report["cohort_index"],0)
        alternate=pilot.run_pilot(market,catalog,major,state,mode="dry-run",
                                  cohort_index=1)
        self.assertEqual(alternate["cohort_index"],1)
        self.assertEqual(report["eligible_sampled_issuer_years"],1)
        self.assertEqual(sum(report["sampled_by_fiscal_year"].values()),1)
        self.assertEqual(sum(report["sampled_by_scope"].values()),1)
        self.assertEqual(sum(report["sampled_by_missing_component"].values()),1)
        self.assertEqual(report["provider_source_calls"],0)
        self.assertEqual(report["unique_exact_name_candidate"],0)
        with self.assertRaises(pilot.PilotError):
            pilot.run_pilot(market,catalog,major,state,mode="live")

if __name__=="__main__":
    unittest.main()
