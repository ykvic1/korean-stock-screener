"""Offline two-market 2026-10-19 reference-PBR observability; no requests."""
import json
import unittest
import pandas as pd
from v3_two_market_pbr_feasibility_once import (
    audit_market_pbr, PBRPilotError, _valid_pbr
)

DAY="2026-10-19"

def catalogue():
    rows=[]
    for i in range(1200):
        rows.append({"code":f"{100000+i:06d}","market":"KOSPI"})
    for i in range(1300):
        rows.append({"code":f"{200000+i:06d}","market":"KOSDAQ"})
    return {"schema":"v22-current-listed-catalog-0.1",
            "date":DAY,"listed_total":len(rows),"rows":rows}

class PBRAvailabilityTests(unittest.TestCase):
    def test_full_current_both_markets_only_derived_counts(self):
        calls=[]
        def provider(day,*,market):
            calls.append((day,market))
            codes=(["100000","100001","100002"] if market=="KOSPI"
                   else ["200000","200001","200002"])
            return pd.DataFrame({"PBR":[1.5,0.0,float("nan")]},index=codes)
        result=audit_market_pbr(DAY,catalogue(),provider=provider)
        self.assertEqual(calls,[("20261019","KOSPI"),("20261019","KOSDAQ")])
        self.assertEqual(result["status"],"observed_research_only")
        self.assertEqual(result["current_listed_total"],2500)
        self.assertEqual(result["current_listed_observed_pbr"],2)
        self.assertEqual(result["market_breakdown"]["KOSDAQ"]["observed_positive_finite_pbr"],1)
        self.assertFalse(result["official_per_stock_pbr_verified"])
        self.assertFalse(result["pbr_became_buy_score"])
        self.assertFalse(result["automated_redistribution_permission_verified"])
        self.assertFalse(result["raw_provider_pbr_saved"])
        body=json.dumps(result)
        self.assertNotIn("100000",body)
        self.assertNotIn("200000",body)
        self.assertNotIn("1.5",body)

    def test_unavailable_market_stays_unverified_not_zero_issuer_pbr(self):
        def provider(day,*,market):
            if market=="KOSDAQ":raise ValueError("PRIVATE_URL_TOKEN_SECRET")
            return pd.DataFrame({"PBR":[1.2]},index=["100000"])
        result=audit_market_pbr(DAY,catalogue(),provider=provider)
        self.assertEqual(result["status"],"partial_or_blocked_provider_research")
        self.assertEqual(result["current_listed_observed_pbr"],1)
        self.assertEqual(result["unavailable_markets"],["KOSDAQ"])
        self.assertNotIn("TOKEN_SECRET",json.dumps(result))

    def test_source_date_and_duplicate_codes_rejected(self):
        c=catalogue()
        with self.assertRaises(PBRPilotError):
            audit_market_pbr("2026-10-18",c,provider=lambda *a,**k:None)
        with self.assertRaises(PBRPilotError):
            audit_market_pbr(DAY,{**c,"rows":c["rows"][:-1]},provider=lambda *a,**k:None)
        dupe=c["rows"][:]
        dupe[1]=dupe[0]
        with self.assertRaises(PBRPilotError):
            audit_market_pbr(DAY,{**c,"rows":dupe},provider=lambda *a,**k:None)
        with self.assertRaises(PBRPilotError):
            audit_market_pbr("2026-02-31",c,provider=lambda *a,**k:None)

    def test_unverified_negative_nan_boolean_and_missing_no_guesses(self):
        for val in (None,False,True,float("inf"),float("nan"),0,-0.1,"—",""):
            self.assertFalse(_valid_pbr(val),repr(val))
        self.assertTrue(_valid_pbr("1.5"))
        self.assertFalse(_valid_pbr(1001))

    def test_provider_duplicate_or_invalid_tickers_not_accepted_as_coverage(self):
        def provider(day,*,market):
            return pd.DataFrame({"PBR":[1.0,2.0]},index=["100000","100000"])
        result=audit_market_pbr(DAY,catalogue(),provider=provider)
        self.assertEqual(result["status"],"partial_or_blocked_provider_research")
        self.assertEqual(result["current_listed_observed_pbr"],0)

if __name__=="__main__":
    unittest.main()
