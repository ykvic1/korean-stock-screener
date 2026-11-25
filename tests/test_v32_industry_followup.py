"""Follow-up decisions are based on saved source changes, not timers."""
import unittest
from unittest.mock import patch
import v32_industry_followup as f


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.market = {"date":"2026-11-24"}
        self.catalog = {"date":"2026-11-24"}
        self.industry = {"asof":"2026-11-24"}
        self.bulk = {"asof":"2026-11-24"}
        h = f._stable_digest
        self.price = {"asof":"2026-11-24","source_sha256":{
            "catalog":h(self.catalog),"market":h(self.market),
            "current_dart_company_industry_codes":h(self.industry)}}
        self.earnings = {"asof":"2026-11-24","source_sha256":{
            "market_catalog":h(self.catalog),"industry_current_company_codes":h(self.industry),
            "dart_annual_receipt_history":h(self.bulk)}}
        self.dual = {"asof":"2026-11-24","groups":[]}
        self.data = {"data.json":self.market,"v22-market-catalog.json":self.catalog,
            "v3-dart-industry-classification.json":self.industry,
            "v3-dart-bulk-history.json":self.bulk,
            "v3-industry-observed-relative-strength.json":self.price,
            "v3-industry-saved-profit-gap-summary.json":self.earnings,
            "v3-industry-dual-evidence-watch.json":self.dual}

    @patch.object(f,"source_ready",return_value=False)
    def test_unready_never_dispatches(self,_):
        for stage in ("classification","price","earnings"):
            self.assertEqual(f.pending_jobs(stage,self.data.get),[])

    @patch.object(f,"complete_saved_gap_source_ready",return_value=True)
    @patch.object(f,"source_ready",return_value=True)
    def test_only_changed_classification_inputs(self,*_):
        self.assertEqual(f.pending_jobs("classification",self.data.get),[])
        self.industry["changed"]=True
        self.assertEqual(f.pending_jobs("classification",self.data.get),[f.PRICE,f.EARNINGS])

    @patch.object(f,"source_ready",return_value=True)
    def test_earnings_can_recover_lagged_price(self,_):
        self.data["v3-industry-observed-relative-strength.json"]=None
        self.assertEqual(f.pending_jobs("earnings",self.data.get),[f.PRICE])

    @patch.object(f,"source_ready",return_value=True)
    def test_dual_only_after_valid_changed_join(self,_):
        with patch.object(f,"dual_build",return_value=self.dual):
            self.assertEqual(f.pending_jobs("price",self.data.get),[])
        with patch.object(f,"dual_build",side_effect=f.CrossSourceError("unaligned")):
            self.assertEqual(f.pending_jobs("price",self.data.get),[])
        with patch.object(f,"dual_build",return_value={"asof":"2026-11-24","groups":[1]}):
            self.assertEqual(f.pending_jobs("price",self.data.get),[f.DUAL])
            self.assertEqual(f.pending_jobs("earnings",self.data.get),[f.DUAL])


if __name__=="__main__":
    unittest.main()
