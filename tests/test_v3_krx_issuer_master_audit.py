"""Pure synthetic audit of KRX market short code against basic-info standard."""
import copy
import json
import unittest

from v3_krx_issuer_master_audit import audit_market_master, MasterJoinError

DAY="2026-10-15"
def stocks():
    return [
        {"BAS_DD":"20261015","ISU_CD":"005930"},
        {"BAS_DD":"20261015","ISU_CD":"00088K"},
        {"BAS_DD":"20261015","ISU_CD":"999999"},
    ]

def master():
    return [
        {"ISU_SRT_CD":"005930","ISU_CD":"KR7005930003"},
        {"ISU_SRT_CD":"00088K","ISU_CD":"KR70008800K0"},
    ]

def audit(t=None,m=None):
    t=stocks() if t is None else t
    m=master() if m is None else m
    return audit_market_master(trade_rows=t,master_rows=m,
        trade_raw=json.dumps({"OutBlock_1":t}).encode(),
        master_raw=json.dumps({"OutBlock_1":m}).encode(),
        market="KOSPI",asof=DAY)

class KRXIssuerMasterTests(unittest.TestCase):
    def test_short_code_mapping_is_safe_and_incomplete_unscored(self):
        r=audit()
        self.assertEqual(r["trade_rows_observed"],3)
        self.assertEqual(r["matched_unique_short_codes"],2)
        self.assertEqual(r["matched_six_character_alphanumeric_codes"],1)
        self.assertEqual(r["unmatched_trade_short_codes"],1)
        self.assertEqual(len(r["master_source_sha256"]),64)
        self.assertFalse(r["same_day_master_pit_independently_verified"])
        self.assertFalse(r["can_release_v3_scoring"])
        packed=json.dumps(r)
        for code in ("005930","00088K","KR7005930003"):
            self.assertNotIn(code,packed)

    def test_wrong_identifier_field_does_not_match(self):
        t=[{"BAS_DD":"20261015","ISU_CD":"005930"}]
        m=[{"ISU_CD":"KR7005930003", "ISU_SRT_CD":"000660"}]
        r=audit(t,m)
        self.assertEqual(r["matched_unique_short_codes"],0)
        self.assertEqual(r["unmatched_trade_short_codes"],1)

    def test_invalid_master_identity_is_not_approved(self):
        m=master()
        m[0]["ISU_CD"]="not-a-standard"
        result=audit(m=m)
        self.assertEqual(result["master_rows_without_valid_standard_code"],1)
        self.assertEqual(result["matched_but_invalid_standard_code"],1)
        self.assertFalse(result["can_release_v3_scoring"])

    def test_duplicate_or_mismatched_master_rejected(self):
        original=master()
        bad_cases=[
            original+[copy.deepcopy(original[0])],
            original+[{"ISU_SRT_CD":"00088L","ISU_CD":original[0]["ISU_CD"]}],
            [{**original[0],"ISU_SRT_CD":"not-six"}],
        ]
        for m in bad_cases:
            with self.subTest(m=m),self.assertRaises(MasterJoinError):
                audit(m=m)

    def test_market_date_and_raw_source_mismatch_rejected(self):
        t=stocks()
        t[0]["BAS_DD"]="20261014"
        with self.assertRaisesRegex(MasterJoinError,"date_mismatch"):
            audit(t=t)
        from v3_krx_issuer_master_audit import audit_market_master
        t=stocks();m=master()
        with self.assertRaisesRegex(MasterJoinError,"rows_disagree"):
            audit_market_master(
                trade_rows=t, master_rows=m, market="KOSPI",asof=DAY,
                trade_raw=b'{"OutBlock_1":[]}',
                master_raw=json.dumps({"OutBlock_1":m}).encode())
if __name__=="__main__":
    unittest.main()
