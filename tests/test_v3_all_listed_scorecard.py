"""No-network all-current-listed output and missing-data invariants."""
import copy
import csv
import json
from io import StringIO
from pathlib import Path
import unittest

import v3_all_listed_scorecard as m
from v3_dart_cashflow_factor_preview import score_preview

DOCS=Path(__file__).resolve().parent.parent/"docs"


def read(name):
    return json.loads((DOCS/name).read_text(encoding="utf8"))


def sources():
    return [read(n) for n in (
        "data.json","v22-market-catalog.json","v3-dart-bulk-history.json",
        "v3-dart-bulk-provisional.json","v3-weekly-rotation.json",
        "v3-dart-cashflow-reference.json","v3-dart-fcf-research-preview.json")]


class AllListedScorecardTests(unittest.TestCase):
    def test_every_catalog_issuer_even_without_usable_price(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        rows,report=m.generate(market,catalog,history,parts,weekly,cash,fcf)
        self.assertEqual(len(rows),catalog["listed_total"])
        self.assertEqual([r["code"] for r in rows],
                         sorted(r["code"] for r in catalog["rows"]))
        self.assertEqual(len({r["code"] for r in rows}),len(rows))
        self.assertEqual(report["full_six_pillar_certified_count"],0)
        self.assertFalse(any(r["factors"]["ias"][1] for r in rows))
        self.assertFalse(any(r["factors"]["industry"][1] for r in rows))
        self.assertTrue(all(r["available"]<=100 and
                            r["unavailable"]==100-r["available"] for r in rows))
        self.assertTrue(any(r["available"]>0 for r in rows))
        self.assertEqual(report["no_numeric_factor_issuer_count"]+
                         report["at_least_one_numeric_factor_count"],len(rows))
        self.assertEqual(report["provisional_numeric_issuer_counts"]["ias"],0)
        self.assertEqual(report["provisional_numeric_issuer_counts"]["industry"],0)
        text=m.to_csv(rows,report)
        self.assertTrue(text.startswith("\ufeff"))
        parsed=list(csv.DictReader(StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(len(parsed),len(rows))
        self.assertEqual(parsed[0]["종목코드"],rows[0]["code"])
        self.assertTrue(all(r["수급_잠정점수"]=="" and
                            r["산업_잠정점수"]=="" and
                            r["정식투자점수"]=="" for r in parsed))
        self.assertTrue(all(r["인증상태"]=="미검증·연구용" for r in parsed))
        never=sum(1 for row in rows if row["available"]==0)
        self.assertEqual(never,report["no_numeric_factor_issuer_count"])
        self.assertTrue(all(not parsed[i]["V3잠정합계"] for i,r in enumerate(rows)
                            if r["available"]==0))
        self.assertTrue(all(parsed[i]["V3잠정합계"]!="" for i,r in enumerate(rows)
                            if r["available"]>0))
        self.assertEqual(report["score_state"],
            "provisional_observed_only_not_investment_grade")

    def test_five_pillar_is_exact_85_without_industry_inflation(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        rows,summary=m.generate(market,catalog,history,parts,weekly,cash,fcf)
        opt=summary["five_area_optional_comparison"]
        self.assertEqual(opt["score_maximum"],85)
        self.assertEqual(sum(opt["weights"].values()),85)
        self.assertNotIn("industry",opt["weights"])
        self.assertTrue(opt["formal_six_area_v3_100_unchanged"])
        self.assertTrue(opt["choice_pending_user_decision"])
        self.assertEqual(opt["certified_investment_scores"],0)
        self.assertEqual(opt["fully_observed_five_area_issuer_count"],0)
        self.assertFalse(any(r["factors"]["industry"][1] for r in rows))
        for r in rows:
            expected=sum(r["factors"][k][1] for k in m.FIVE_AREAS)
            self.assertEqual(r["five_area_available"],expected)
            self.assertEqual(r["five_area_missing"],85-expected)
            self.assertTrue(0<=expected<=85)
            self.assertEqual(r["five_area_85_points"],r["earned"])
            self.assertFalse(r["five_area_complete_unverified"])
        generated=list(csv.DictReader(StringIO(m.to_csv(
            rows,summary).lstrip("\ufeff"))))
        self.assertEqual(len(generated),summary["listed_total"])
        for got,source in zip(generated,rows):
            self.assertEqual(got["5영역_실제산출배점_85점중"],
                             str(source["five_area_available"]))
            self.assertEqual(got["5영역_미확보배점_85점중"],
                             str(source["five_area_missing"]))
            self.assertEqual(got["5영역_산업제외_잠정점수_85점중"],
                             got["V3잠정합계"])
            self.assertEqual(got["산업_정성분류_점수없음"],
                             source["industry_label_unverified"] or "")
            self.assertEqual(got["정식투자점수"],"")

    def test_fcf_only_when_exact_recomputed_source_matches(self):
        market,catalog,history,parts,weekly,cash,fcf=sources()
        _,base=m.generate(market,catalog,history,parts,weekly)
        rows,real=m.generate(market,catalog,history,parts,weekly,cash,fcf)
        self.assertTrue(real["fcf_source_added"] in (True,False))
        tampered=copy.deepcopy(fcf)
        if tampered["rows"]:
            tampered["rows"][0]["scoring_certified"]=True
            other,report=m.generate(
                market,catalog,history,parts,weekly,cash,tampered)
            self.assertFalse(report["fcf_source_added"])
            self.assertEqual(report["provisional_numeric_issuer_counts"],
                             base["provisional_numeric_issuer_counts"])
            self.assertEqual(other[0]["factors"]["ias"],(None,0))
        future=copy.deepcopy(fcf)
        future["asof"]="2026-10-22"
        _,blocked=m.generate(market,catalog,history,parts,weekly,cash,future)
        self.assertFalse(blocked["fcf_source_added"])
        self.assertIsNone(blocked["source_sha256"]["fcf"])
        # No certification is produced regardless of whether independently
        # stored annual receipts are still aligned with the moving major book.
        self.assertEqual(real["full_six_pillar_certified_count"],0)

    def test_older_fcf_preview_recalculated_from_saved_real_receipts(self):
        market,catalog,history,parts,weekly,cash,_=sources()
        current=score_preview(market,catalog,history,cash,
                              minimum_peers=30)
        stale=copy.deepcopy(current)
        stale["major_history_sha256"]="0"*64
        rows,report=m.generate(
            market,catalog,history,parts,weekly,cash,stale)
        self.assertTrue(report["fcf_source_added"])
        self.assertEqual(report["fcf_source_state"],
                         "recomputed_from_saved_verified_cashflow")
        self.assertEqual(report["source_sha256"]["fcf"],
                         m._stable_digest(current))
        self.assertEqual(len(rows),catalog["listed_total"])
        self.assertEqual(report["full_six_pillar_certified_count"],0)
        unsafe=copy.deepcopy(stale)
        unsafe["rows"][0]["scoring_certified"]=True
        _,blocked=m.generate(market,catalog,history,parts,weekly,cash,unsafe)
        self.assertFalse(blocked["fcf_source_added"])

    def test_missing_and_invalid_factors_do_not_become_zero(self):
        self.assertEqual(m._contribution(None,0,15),(None,0))
        self.assertEqual(m._contribution(0,5,15),(0.0,5))
        for wrong in ((7,0,15),(16,15,15),(None,4,15),
                      (10,16,15),(-1,4,15)):
            with self.assertRaises(m.FullMarketScorecardError):
                m._contribution(*wrong)
        self.assertEqual(m._momentum({}, "2026-10-20","KOSPI"),(None,0))
        market,catalog,history,parts,weekly,cash,fcf=sources()
        broken=copy.deepcopy(parts)
        if broken["rows"]:
            broken["rows"][0]["combined_available_max"]=100
            with self.assertRaises(Exception):
                m.generate(market,catalog,history,broken,weekly)
        incomplete=copy.deepcopy(weekly)
        incomplete["numeric_technical_candidates_this_cycle"]+=1
        with self.assertRaises(Exception):
            m.generate(market,catalog,history,parts,incomplete)


if __name__=="__main__":
    unittest.main()
