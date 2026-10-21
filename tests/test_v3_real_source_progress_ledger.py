"""Six-area source acquisition trend is not six investable scores."""
import copy
import json
from datetime import date,timedelta
from pathlib import Path
import tempfile
import unittest

import v3_real_source_progress_ledger as ledger


def real_audit():
    path=Path(__file__).resolve().parent.parent / "docs" / \
         "v3-real-source-six-area-coverage.json"
    return json.loads(path.read_text(encoding="utf8"))


class SixAreaProgressLedgerTests(unittest.TestCase):
    def test_real_source_identity_append_and_idempotence(self):
        audit=real_audit()
        observation=ledger.observation(audit)
        self.assertEqual(observation["market_asof"],audit["market_asof"])
        self.assertEqual(observation["listed_total"],audit["listed_total"])
        self.assertEqual(observation["official_full_six_pillar_scores"],0)
        self.assertEqual(observation["provisional_issuers_by_area"]["ias"],0)
        self.assertEqual(observation["provisional_issuers_by_area"]["industry"],0)
        self.assertFalse(observation["historical_PIT_certified"])
        self.assertEqual(len(observation["audit_sha256"]),64)
        first=ledger.append(audit)
        self.assertEqual(len(first["snapshots"]),1)
        self.assertEqual(first["last_sequence"],1)
        self.assertIsNone(first["snapshots"][0]["progress_since_previous"])
        self.assertEqual(ledger.append(audit,copy.deepcopy(first)),first)

    def test_source_revision_adds_only_actual_delta_and_never_claims_full(self):
        audit=real_audit()
        earlier=ledger.append(audit)
        later=copy.deepcopy(audit)
        later["six_pillar_numeric_provisional_issuer_counts"]["financial"]+=1
        later["three_year_actual_margin_source_issuers"]+=1
        later["three_year_margin_provisional_score_issuers"]+=1
        updated=ledger.append(later,earlier)
        self.assertEqual(updated["last_sequence"],2)
        self.assertEqual(len(updated["snapshots"]),2)
        self.assertEqual(updated["snapshots"][-1]["progress_since_previous"],
                         {k:(1 if k=="financial" else 0) for k in ledger.AREAS})
        self.assertTrue(updated["snapshots"][-1]["same_market_asof_as_previous"])
        self.assertFalse(updated["buy_signals_authorized"])
        self.assertEqual(ledger.append(later,updated),updated)
        future=copy.deepcopy(later)
        future["market_asof"]=(date.fromisoformat(later["market_asof"])+
                               timedelta(days=1)).isoformat()
        future["six_pillar_numeric_provisional_issuer_counts"]["financial"]-=1
        shifted=ledger.append(future,updated)
        self.assertIsNone(shifted["snapshots"][-1]["progress_since_previous"])
        self.assertFalse(shifted["snapshots"][-1]["same_market_asof_as_previous"])
        with self.assertRaisesRegex(ledger.ProgressLedgerError,
                                     "previous_source_date_ahead"):
            ledger.append(audit,shifted)

    def test_fake_six_factor_complete_missing_year_and_source_hash_rejected(self):
        audit=real_audit()
        bad=copy.deepcopy(audit)
        bad["six_pillar_official_complete_100pt_issuer_count"]=1
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"uncertified"):
            ledger.observation(bad)
        bad=copy.deepcopy(audit)
        bad["six_pillar_numeric_provisional_issuer_counts"]["ias"]=12
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"area_counts"):
            ledger.observation(bad)
        bad=copy.deepcopy(audit)
        bad["source_sha256_canonical"]["dart_four_year_archive"]="0"*12
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"fingerprints"):
            ledger.observation(bad)
        bad=copy.deepcopy(audit)
        bad["historical_dart_company_years_with_accounts_by_fiscal"]["2022"]=9999
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"fiscal_coverage"):
            ledger.observation(bad)
        bad=copy.deepcopy(audit)
        bad["investment_grade_scores_confirmed"]=True
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"uncertified"):
            ledger.observation(bad)

    def test_corrupt_previous_and_source_date_rollbacks_fail_closed(self):
        audit=real_audit()
        first=ledger.append(audit)
        corrupt=copy.deepcopy(first)
        corrupt["last_sequence"]=99
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"sequence"):
            ledger.append(audit,corrupt)
        corrupt=copy.deepcopy(first)
        corrupt["snapshots"][0]["observation"]["audit_sha256"]="not-a-sha"
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"audit_hash"):
            ledger.append(audit,corrupt)
        corrupt=copy.deepcopy(first)
        corrupt["buy_signals_authorized"]=True
        with self.assertRaisesRegex(ledger.ProgressLedgerError,"untrusted_previous"):
            ledger.append(audit,corrupt)
        self.assertEqual(ledger.append(audit,first),first)

    def test_offline_cli_write_then_verify_and_mutation_detect(self):
        with tempfile.TemporaryDirectory() as temp:
            audit=real_audit()
            ad=Path(temp)/"audit.json"
            pd=Path(temp)/"progress.json"
            ad.write_text(json.dumps(audit),encoding="utf8")
            ledger.main(["--audit",str(ad),"--progress",str(pd),"--mode","write"])
            self.assertTrue(pd.is_file())
            ledger.main(["--audit",str(ad),"--progress",str(pd),"--mode","verify"])
            mutated=copy.deepcopy(audit)
            mutated["three_or_more_provisional_numerical_areas"]+=1
            ad.write_text(json.dumps(mutated),encoding="utf8")
            with self.assertRaisesRegex(ledger.ProgressLedgerError,"stale"):
                ledger.main(["--audit",str(ad),"--progress",str(pd),
                             "--mode","verify"])
            ledger.main(["--audit",str(ad),"--progress",str(pd),"--mode","write"])
            ledger.main(["--audit",str(ad),"--progress",str(pd),"--mode","verify"])

if __name__=="__main__":
    unittest.main()
