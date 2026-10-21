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
