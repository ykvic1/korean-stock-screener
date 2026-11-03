"""Individual factor retention must survive additions, removals and lags."""
import copy
import unittest
from pathlib import Path

from v3_issuer_factor_ledger import build, IssuerFactorLedgerError, SCHEMA
from v3_real_six_pillar_coverage_audit import _canonical_hash


def member(code,name,market="KOSPI"):
    return {"code":code,"name":name,"market":market}


def row(company,day,*,financial=5,valuation=4):
    factors={"volume":6,"technical":12,"financial":financial,
             "valuation":valuation,"momentum":3}
    weight={"volume":16,"technical":25,"financial":8 if financial is not None else 0,
            "valuation":10 if valuation is not None else 0,"momentum":8}
    return {**company,"asof":day,"factors":factors,"factor_available":weight}


def census(day,companies):
    return {"date":day,"listed_total":len(companies),"rows":companies}


class IssuerFactorRetentionTests(unittest.TestCase):
    def test_new_listing_and_delisting_do_not_wipe_other_issuers(self):
        a=member("005930","A");b=member("000020","B")
        day1="2026-10-30";day2="2026-11-02"
        first=build(census(day1,[a,b]),[row(a,day1),row(b,day1)])
        self.assertEqual(first["rows"][a["code"]]["factors"]["financial"]["points"],5)
        c=member("004000","신규")
        later=build(census(day2,[a,c]),
                    [row(a,day2,financial=None,valuation=None),
                     row(c,day2,financial=None,valuation=None)],
                    first)
        old=later["rows"][a["code"]]["factors"]
        self.assertEqual(old["financial"]["asof"],day1)
        self.assertEqual(old["valuation"]["asof"],day1)
        self.assertEqual(old["technical"]["asof"],day2)
        self.assertEqual(later["rows"][c["code"]]["factors"].get("financial"),None)
        self.assertFalse(later["rows"][b["code"]]["active"])
        self.assertEqual(later["active_issuer_count"],2)
        self.assertEqual(later["inactive_issuer_count"],1)
        self.assertTrue(later["mixed_date_sum_permitted"])
        self.assertTrue(later["mixed_date_score_research_only"])
        self.assertFalse(later["investment_score_certified"])
        self.assertEqual(later["schema"],SCHEMA)
        self.assertEqual(build(census(day2,[a,c]),
                         [row(a,day2,financial=None,valuation=None),
                          row(c,day2,financial=None,valuation=None)],later),later)

    def test_narrower_new_partial_factor_keeps_older_broader_coverage(self):
        a=member("005930","A")
        prior_row=row(a,"2026-10-30",financial=10,valuation=11)
        prior_row["factor_available"]["financial"]=16
        prior_row["factor_available"]["valuation"]=15
        original=build(census("2026-10-30",[a]),[prior_row])
        newer=row(a,"2026-11-02",financial=3,valuation=4)
        # Newer 8/10 point subsets must not erase valid 16/15 point subsets.
        retained=build(census("2026-11-02",[a]),[newer],original)
        factors=retained["rows"][a["code"]]["factors"]
        self.assertEqual(factors["financial"]["asof"],"2026-10-30")
        self.assertEqual(factors["financial"]["points"],10)
        self.assertEqual(factors["valuation"]["asof"],"2026-10-30")
        self.assertEqual(factors["valuation"]["points"],11)
        self.assertEqual(factors["volume"]["asof"],"2026-11-02")
        # When same or better coverage returns, only that factor refreshes.
        improved=row(a,"2026-11-03",financial=13,valuation=9)
        improved["factor_available"]["financial"]=16
        improved["factor_available"]["valuation"]=15
        updated=build(census("2026-11-03",[a]),[improved],retained)
        self.assertEqual(updated["rows"][a["code"]]["factors"]["financial"]["points"],13)
        self.assertEqual(updated["rows"][a["code"]]["factors"]["valuation"]["asof"],"2026-11-03")
        old_contract=copy.deepcopy(retained)
        old_contract["mixed_date_sum_permitted"]=False
        migrated=build(census("2026-11-03",[a]),[improved],old_contract)
        self.assertTrue(migrated["mixed_date_sum_permitted"])

    def test_same_ticker_name_change_does_not_inherit_old_score(self):
        a=member("005930","Old Co")
        old=build(census("2026-10-30",[a]),[row(a,"2026-10-30")])
        renamed=member("005930","New Co")
        current=build(census("2026-11-02",[renamed]),
                      [row(renamed,"2026-11-02",financial=None,valuation=None)],old)
        self.assertNotIn("financial",current["rows"]["005930"]["factors"])
        self.assertNotIn("valuation",current["rows"]["005930"]["factors"])

    def test_rename_retains_old_scores_as_nonranking_historical_identity(self):
        old_issuer=member("005930","Former Name")
        renamed=member("005930","New Name")
        prior=build(census("2026-10-30",[old_issuer]),
                    [row(old_issuer,"2026-10-30")],
                    fiscal_rows={"005930":{
                        "code":"005930","market":"KOSPI","asof":"2026-10-30",
                        "unverified_components":{"pbr":{
                            "max_points":5,"candidate_points":3.5}}}})
        latest=build(census("2026-11-02",[renamed]),
                     [row(renamed,"2026-11-02",financial=None,valuation=None)],
                     prior,fiscal_rows={"005930":{
                        "code":"005930","market":"KOSPI","asof":"2026-11-02",
                        "unverified_components":{"pbr":{
                            "max_points":5,"candidate_points":1.5}}}})
        current=latest["rows"]["005930"]
        self.assertNotIn("financial",current["factors"])
        self.assertNotIn("valuation",current["factors"])
        self.assertFalse(current["components"])
        archived=list(latest["identity_review_archive"].values())
        self.assertEqual(len(archived),1)
        old=archived[0]
        self.assertEqual(old["name"],"Former Name")
        self.assertEqual(old["last_known_market_asof"],"2026-10-30")
        self.assertEqual(old["factors"]["financial"]["points"],5)
        self.assertEqual(old["components"]["valuation"]["pbr"]["points"],3.5)
        self.assertFalse(old["identity_independently_authenticated"])
        self.assertFalse(old["eligible_for_current_ranking"])
        self.assertTrue(latest["identity_history_unverified_not_ranked"])
        replay=build(census("2026-11-02",[renamed]),
                     [row(renamed,"2026-11-02",financial=None,valuation=None)],
                     latest)
        self.assertEqual(latest["identity_review_archive"],
                         replay["identity_review_archive"])
        self.assertEqual(replay["rows"],latest["rows"])

    def test_delisted_ticker_reuse_or_market_transfer_keeps_prior_history(self):
        old=member("005930","Issuer 1")
        first=build(census("2026-10-30",[old]),[row(old,"2026-10-30")])
        inactive=build(census("2026-11-02",[]),[],first)
        self.assertFalse(inactive["rows"]["005930"]["active"])
        reissued=member("005930","Issuer 2")
        current=build(census("2026-11-03",[reissued]),
                      [row(reissued,"2026-11-03",financial=None,valuation=None)],
                      inactive)
        self.assertEqual(len(current["identity_review_archive"]),1)
        self.assertNotIn("financial",current["rows"]["005930"]["factors"])
        self.assertEqual(next(iter(current["identity_review_archive"].values()))
                         ["factors"]["financial"]["asof"],"2026-10-30")
        transferred=member("005930","Issuer 2","KOSDAQ")
        later=build(census("2026-11-04",[transferred]),
                    [row(transferred,"2026-11-04",
                         financial=None,valuation=None)],current)
        self.assertEqual(len(later["identity_review_archive"]),2)
        self.assertNotIn("financial",later["rows"]["005930"]["factors"])
        self.assertEqual(later["rows"]["005930"]["market"],"KOSDAQ")

    def test_tampered_retired_identity_history_fails_closed_and_no_false_pit(self):
        old=member("005930","Old")
        new=member("005930","New")
        first=build(census("2026-10-30",[old]),[row(old,"2026-10-30")])
        second=build(census("2026-11-02",[new]),
                     [row(new,"2026-11-02",financial=None,valuation=None)],first)
        corrupt=copy.deepcopy(second)
        archived=next(iter(corrupt["identity_review_archive"].values()))
        archived["factors"]["financial"]["points"]=20
        with self.assertRaisesRegex(IssuerFactorLedgerError,
                                    "invalid_retired_factor_data"):
            build(census("2026-11-03",[new]),
                  [row(new,"2026-11-03",financial=None,valuation=None)],corrupt)
        forged=copy.deepcopy(second)
        archived=next(iter(forged["identity_review_archive"].values()))
        archived["identity_independently_authenticated"]=True
        with self.assertRaisesRegex(IssuerFactorLedgerError,
                                    "altered_identity_review_archive"):
            build(census("2026-11-03",[new]),
                  [row(new,"2026-11-03",financial=None,valuation=None)],forged)

    def test_missing_is_not_zero_but_observed_zero_is_valid(self):
        a=member("005930","A")
        same=row(a,"2026-11-02",financial=0,valuation=None)
        saved=build(census("2026-11-02",[a]),[same])
        self.assertEqual(saved["rows"]["005930"]["factors"]["financial"]["points"],0.0)
        self.assertNotIn("valuation",saved["rows"]["005930"]["factors"])

    def test_dated_fiscal_archive_bootstraps_without_a_new_day_score(self):
        a=member("005930","A"); day="2026-11-02";old="2026-10-30"
        prior_parts={"schema":"v3-dart-multi-year-research-parts-0.1",
            "asof":old,"listed_total":1,"historical_PIT_certified":False,
            "all_scores_provisional_unverified":True,
            "fully_verified_six_pillar_count":0,
            "rows":[{"code":"005930","market":"KOSPI","asof":old,
                     "confirmed_v3_score":None,"scoring_certified":False,
                     "financial_available_max":8,
                     "valuation_available_max":10,
                     "financial_candidate_points":5.5,
                     "valuation_candidate_points":7.2}]}
        audit={"schema":"v3-real-saved-source-six-pillar-coverage-0.1",
            "market_asof":old,"listed_total":1,
            "investment_grade_scores_confirmed":False,
            "six_pillar_official_complete_100pt_issuer_count":0,
            "source_sha256_canonical":{"dart_partial_score":_canonical_hash(prior_parts)},
            "six_pillar_numeric_provisional_issuer_counts":
                {"financial":1,"valuation":1}}
        live=row(a,day,financial=None,valuation=None)
        saved=build(census(day,[a]),[live],parts=prior_parts,audit=audit)
        self.assertEqual(saved["rows"]["005930"]["factors"]["financial"]["asof"],old)
        self.assertEqual(saved["rows"]["005930"]["factors"]["valuation"]["points"],7.2)
        broken=copy.deepcopy(audit)
        broken["source_sha256_canonical"]["dart_partial_score"]="0"*64
        untrusted=build(census(day,[a]),[live],parts=prior_parts,audit=broken)
        self.assertNotIn("financial",untrusted["rows"]["005930"]["factors"])
        self.assertFalse(untrusted["investment_score_certified"])

    def test_bad_identity_and_forged_current_points_fail_closed(self):
        a=member("005930","A");day="2026-11-02"
        bad=row(a,day);bad["factor_available"]["valuation"]=0
        with self.assertRaisesRegex(IssuerFactorLedgerError,
                                    "unacquired_factor_has_points"):
            build(census(day,[a]),[bad])
        good=build(census(day,[a]),[row(a,day)])
        forged=copy.deepcopy(good)
        forged["investment_score_certified"]=True
        with self.assertRaisesRegex(IssuerFactorLedgerError,
                                    "untrusted_prior_factor_ledger"):
            build(census(day,[a]),[row(a,day)],forged)

    def test_fiscal_and_fcf_components_keep_independent_values_and_dates(self):
        a=member("005930","A"); old="2026-10-30";today="2026-11-02"
        fiscal={"code":a["code"],"market":a["market"],"asof":old,
            "unverified_components":{
                "revenue_cagr_3y_pct":{"max_points":4,"candidate_points":2.5},
                "operating_margin_3y_pct":{"max_points":4,"candidate_points":0},
                "pbr":{"max_points":5,"candidate_points":3.1},
                "earnings_yield_pct":{"max_points":0,"candidate_points":None}}}
        fcf={"code":a["code"],"market":a["market"],"asof":old,
            "strict_receipt_scope_matched":True,"scoring_certified":False,
            "historical_PIT_certified":False,
            "financial_fcf_assets_available_max":4,
            "financial_fcf_assets_candidate_points":1.3,
            "valuation_fcf_yield_available_max":5,
            "valuation_fcf_yield_candidate_points":0}
        first=build(census(old,[a]),[row(a,old)],
                    fiscal_rows={a["code"]:fiscal},fcf_rows={a["code"]:fcf})
        observed=first["rows"][a["code"]]["components"]
        self.assertEqual(observed["financial"]["operating_margin_3y_pct"]["points"],0)
        self.assertEqual(observed["valuation"]["fcf_yield"]["points"],0)
        self.assertNotIn("earnings_yield_pct",observed["valuation"])
        self.assertTrue(first["component_ledger_research_only"])
        self.assertFalse(first["investment_score_certified"])
        self.assertEqual(first["rows"][a["code"]]["factors"]["financial"]["points"],5)
        refreshed=copy.deepcopy(fiscal)
        refreshed["asof"]=today
        refreshed["unverified_components"]={
            "revenue_cagr_3y_pct":{"max_points":4,"candidate_points":3.5}}
        later=build(census(today,[a]),[row(a,today,financial=None,valuation=None)],
                    first,fiscal_rows={a["code"]:refreshed})
        got=later["rows"][a["code"]]
        self.assertEqual(got["components"]["financial"]["revenue_cagr_3y_pct"]["asof"],today)
        self.assertEqual(got["components"]["financial"]["revenue_cagr_3y_pct"]["points"],3.5)
        self.assertEqual(got["components"]["financial"]["operating_margin_3y_pct"]["asof"],old)
        self.assertEqual(got["components"]["financial"]["fcf_assets_3y"]["asof"],old)
        self.assertEqual(got["components"]["valuation"]["fcf_yield"]["asof"],old)
        self.assertEqual(got["factors"]["financial"]["asof"],old)
        self.assertEqual(got["factors"]["financial"]["points"],5)
        self.assertEqual(build(census(today,[a]),
            [row(a,today,financial=None,valuation=None)],later),later)

    def test_same_day_legacy_snapshot_cannot_replay_over_newer_dart_receipt(self):
        """A repeated publication must not invent same-day change history."""
        issuer=member("005930","A")
        day="2026-11-03"
        code=issuer["code"]
        def fiscal(points):
            return {code:{"code":code,"market":"KOSPI","asof":day,
                "unverified_components":{"pbr":{
                    "max_points":5,"candidate_points":points}}}}
        # A prior valid current receipt supersedes a lower-priority archived
        # subfactor even when both carry the same market reference date.
        legacy={"schema":"v3-dart-multi-year-research-parts-0.1",
            "asof":day,"listed_total":1,"historical_PIT_certified":False,
            "all_scores_provisional_unverified":True,
            "fully_verified_six_pillar_count":0,
            "rows":[{"code":code,"market":"KOSPI","asof":day,
                     "confirmed_v3_score":None,"scoring_certified":False,
                     "financial_available_max":0,"valuation_available_max":0,
                     "financial_candidate_points":None,
                     "valuation_candidate_points":None,
                     "unverified_components":{"pbr":{
                         "max_points":5,"candidate_points":1.0}}}]}
        audit={"schema":"v3-real-saved-source-six-pillar-coverage-0.1",
            "market_asof":day,"listed_total":1,
            "investment_grade_scores_confirmed":False,
            "six_pillar_official_complete_100pt_issuer_count":0,
            "source_sha256_canonical":{"dart_partial_score":_canonical_hash(legacy)},
            "six_pillar_numeric_provisional_issuer_counts":
                {"financial":0,"valuation":0}}
        market=census(day,[issuer])
        current=[row(issuer,day)]
        first=build(market,current,fiscal_rows=fiscal(2.0))
        updated=build(market,current,first,parts=legacy,audit=audit,
                      fiscal_rows=fiscal(3.0))
        component=updated["rows"][code]["components"]["valuation"]["pbr"]
        history=updated["rows"][code]["component_history"]["valuation"]["pbr"]
        self.assertEqual(component["points"],3.0)
        self.assertEqual(history,[{"asof":day,"points":2.0}])
        self.assertEqual(
            build(market,current,updated,parts=legacy,audit=audit,
                  fiscal_rows=fiscal(3.0)),updated)

    def test_each_component_tracks_last_three_changes_without_touching_score(self):
        a=member("005930","A")
        def fiscal(day,points):
            return {"code":a["code"],"market":a["market"],"asof":day,
                    "unverified_components":{"pbr":{
                        "max_points":5,"candidate_points":points}}}
        dates=["2026-10-29","2026-10-30","2026-11-02",
               "2026-11-03","2026-11-04"]
        saved=None
        for index,day in enumerate(dates):
            live=row(a,day,financial=None,valuation=None)
            new=fiscal(day,1+index*0.5)
            saved=build(census(day,[a]),[live],saved,
                        fiscal_rows={a["code"]:new})
            sub=saved["rows"][a["code"]]
            self.assertEqual(sub["components"]["valuation"]["pbr"]["points"],
                             1+index*0.5)
            self.assertNotIn("valuation",sub["factors"])
            history=sub["component_history"].get("valuation",{}).get("pbr",[])
            self.assertEqual(len(history),min(index,3))
        self.assertEqual([e["asof"] for e in history],dates[1:4])
        self.assertEqual([e["points"] for e in history],[1.5,2,2.5])
        repeated=build(census(dates[-1],[a]),
                       [row(a,dates[-1],financial=None,valuation=None)],
                       saved,fiscal_rows={a["code"]:fiscal(dates[-1],3)})
        self.assertEqual(repeated,saved,
                         "identical daily replay must not add history events")
        offline=build(census("2026-11-05",[a]),
                      [row(a,"2026-11-05",financial=None,valuation=None)],
                      saved)
        self.assertEqual(offline["rows"][a["code"]]["component_history"],
                         saved["rows"][a["code"]]["component_history"])
        self.assertEqual(offline["rows"][a["code"]]["components"]["valuation"]["pbr"]["asof"],
                         "2026-11-04")
        # A malformed provenance history cannot remove good current subfactors.
        forged=copy.deepcopy(saved)
        forged["rows"][a["code"]]["component_history"]["valuation"]["pbr"]=[
            {"asof":"2027-01-01","points":99}]
        clean=build(census("2026-11-05",[a]),
                    [row(a,"2026-11-05",financial=None,valuation=None)],forged)
        self.assertEqual(clean["rows"][a["code"]]["component_history"],{})
        self.assertEqual(clean["rows"][a["code"]]["components"]["valuation"]["pbr"]["points"],3)

    def test_malformed_individual_history_cannot_poison_retained_score(self):
        a=member("005930","A");day="2026-11-02"
        original=build(census(day,[a]),[row(a,day)])
        forged=copy.deepcopy(original)
        forged["rows"][a["code"]]["components"]={"financial":{
            "pbr":{"asof":day,"available":5,"points":5,
                   "source":"dart_fcf_subfactor"}}}
        restored=build(census(day,[a]),[row(a,day)],forged)
        self.assertEqual(restored["rows"][a["code"]]["factors"],
                         original["rows"][a["code"]]["factors"])
        self.assertEqual(restored["rows"][a["code"]]["components"],{})
        forged["rows"][a["code"]]["components"]=["malformed"]
        restored=build(census(day,[a]),[row(a,day)],forged)
        self.assertEqual(restored["rows"][a["code"]]["components"],{})
        invalid={"code":a["code"],"market":a["market"],"asof":day,
                 "unverified_components":{"pbr":{
                     "max_points":6,"candidate_points":4}}}
        with self.assertRaisesRegex(IssuerFactorLedgerError,
                                    "invalid_fiscal_component_weight"):
            build(census(day,[a]),[row(a,day)],
                  fiscal_rows={a["code"]:invalid})
        self.assertEqual(original["rows"][a["code"]]["factors"]["valuation"]["points"],4)

    def test_actual_collector_is_the_sole_publication_writer(self):
        root=Path(__file__).resolve().parent.parent
        workflow=(root/".github/workflows/v3-current-observed-scorecard.yml"
                 ).read_text(encoding="utf8")
        worker=(root/"v3_current_observed_scorecard.py").read_text(encoding="utf8")
        self.assertIn("docs/v3-issuer-last-observed-factors.json",workflow)
        self.assertIn("build_issuer_factor_ledger(",worker)
        self.assertIn("fiscal_rows=financial,fcf_rows=cashflow",worker)
        self.assertIn("or json.loads(ledger_path.read_text",worker)


if __name__=="__main__":
    unittest.main()
