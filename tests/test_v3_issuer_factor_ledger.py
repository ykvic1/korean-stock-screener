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
        self.assertFalse(later["mixed_date_sum_permitted"])
        self.assertFalse(later["investment_score_certified"])
        self.assertEqual(later["schema"],SCHEMA)
        self.assertEqual(build(census(day2,[a,c]),
                         [row(a,day2,financial=None,valuation=None),
                          row(c,day2,financial=None,valuation=None)],later),later)

    def test_same_ticker_name_change_does_not_inherit_old_score(self):
        a=member("005930","Old Co")
        old=build(census("2026-10-30",[a]),[row(a,"2026-10-30")])
        renamed=member("005930","New Co")
        current=build(census("2026-11-02",[renamed]),
                      [row(renamed,"2026-11-02",financial=None,valuation=None)],old)
        self.assertNotIn("financial",current["rows"]["005930"]["factors"])
        self.assertNotIn("valuation",current["rows"]["005930"]["factors"])

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

    def test_actual_collector_is_the_sole_publication_writer(self):
        root=Path(__file__).resolve().parent.parent
        workflow=(root/".github/workflows/v3-current-observed-scorecard.yml"
                 ).read_text(encoding="utf8")
        worker=(root/"v3_current_observed_scorecard.py").read_text(encoding="utf8")
        self.assertIn("docs/v3-issuer-last-observed-factors.json",workflow)
        self.assertIn("build_issuer_factor_ledger(",worker)
        self.assertIn("or json.loads(ledger_path.read_text",worker)


if __name__=="__main__":
    unittest.main()
