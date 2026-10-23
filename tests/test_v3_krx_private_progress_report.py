"""Private derived KRX progress publication must be aggregated and fail-closed."""
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from v3_krx_derived_backfill import _empty as source_empty, _path as source_path
from v3_krx_derived_bridges import _link_path as bridge_path
from v3_krx_derived_rolling_returns import _file as return_path
from v3_krx_private_progress_report import (
    PrivateProgressError, collect, PRIVATE_REPO,
)

DIGEST="a"*64
DAYS=[(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(60)]


class FakePrivate:
    repository=PRIVATE_REPO
    def __init__(self,entries):
        self.entries=entries
        self.verified=0
        self.reads=[]
        self.replace_source_on_second_read=False
    def verify_private(self):
        self.verified+=1
        return True
    def get_content(self,path):
        self.reads.append(path)
        blob=self.entries.get(path)
        if self.replace_source_on_second_read and path=="derived/historical-checkpoint.json":
            if self.reads.count(path)>1:
                blob=b"tampered"
        return blob, sha256(blob).hexdigest() if blob is not None else None


def saved_source(n=12):
    row=source_empty(DIGEST)
    row["next_window_index"]=n
    row["last_window_file_sha256"]=sha256(b"derived-source").hexdigest()
    return json.dumps(row).encode("utf8")


def saved_bridge(next_index=11):
    row={
        "schema":"v3-krx-private-boundary-progress-v1",
        "calendar_candidate_hash":DIGEST,
        "next_boundary_index":next_index,
        "cumulative_derived_boundary_issuer_records":0,
        "cumulative_compressed_bytes":0,
        "last_boundary_sha256":sha256(b"derived-bridge").hexdigest(),
        "original_source_files_stored":0,
    }
    return json.dumps(row).encode("utf8")


def saved_rolling():
    row={
        "schema":"v3-krx-private-rolling-cohort-return-progress-v1",
        "calendar_candidate_hash":DIGEST,
        "horizon":20, "next_end_bucket":10,
        "complete_issuer_window_observations":44,
        "compressed_derived_bytes":5000,
        "last_window_sha256":sha256(b"derived-rolling").hexdigest(),
        "vendor_raw_file_count":0,
    }
    return json.dumps(row).encode("utf8")


def saved_risk(cursor=10):
    row={
        "schema":"v3-private-krx-incremental-price-risk-v1",
        "calendar_candidate_hash":DIGEST,
        "next_cohort_index":cursor,
        "historical_candidate_last_day":DAYS[5*cursor-1],
        "cumulative_price_discontinuity_flags":3,
        "cumulative_issuer_buckets_examined":0,
        "cumulative_issuer_bridges_examined":0,
        "cumulative_quality_compressed_bytes":100,
        "last_audit_file":"derived/quality/batches/fixture.json.gz",
        "last_audit_sha256":sha256(b"derived-risk").hexdigest(),
        "raw_provider_files_stored":0,
    }
    return json.dumps(row).encode("utf8")


def fixture():
    entries={
        "derived/historical-checkpoint.json":saved_source(),
        source_path(DAYS[55:60]):b"derived-source",
        "derived/boundaries-checkpoint.json":saved_bridge(),
        bridge_path(DAYS[49],DAYS[50]):b"derived-bridge",
        "derived/rolling/20/checkpoint.json":saved_rolling(),
        return_path(DAYS[25],DAYS[44],20):b"derived-rolling",
        "derived/quality/incremental-checkpoint.json":saved_risk(),
        "derived/quality/batches/fixture.json.gz":b"derived-risk",
    }
    return FakePrivate(entries)


def run(fake):
    with (patch("v3_krx_private_progress_report.validate_derived_blob"),
          patch("v3_krx_private_progress_report.validate_bridge"),
          patch("v3_krx_private_progress_report.validate_return")):
        return collect(fake,DAYS,DIGEST)


class CheckPrivateProgress(unittest.TestCase):
    def test_all_four_stages_are_real_checkpoint_counts(self):
        fake=fixture()
        result=run(fake)
        self.assertEqual(fake.verified,1)
        self.assertEqual(result["five_session_cohorts_completed"],12)
        self.assertEqual(result["five_session_cohorts_remaining"],0)
        self.assertEqual(result["cross_cohort_bridges_completed"],10)
        self.assertEqual(result["cross_cohort_bridges_backlog"],1)
        h=result["rolling_return_horizon_research"]
        self.assertEqual(h["20"]["complete_cohort_windows"],6)
        self.assertEqual(h["20"]["possible_windows_from_current_verified_bridges"],8)
        self.assertEqual(h["60"]["complete_cohort_windows"],0)
        self.assertEqual(result["price_discontinuity_heuristic_cohorts_reviewed"],10)
        self.assertEqual(result["price_discontinuity_heuristic_flag_events_observed"],3)
        self.assertEqual(result["price_discontinuity_issuer_cohort_observations_examined"],0)
        self.assertEqual(result["price_discontinuity_issuer_boundary_observations_examined"],0)
        self.assertIs(result["price_discontinuity_flags_are_confirmed_corporate_actions"],False)
        self.assertEqual(result["price_discontinuity_review_backlog_at_verified_bridges"],1)
        self.assertFalse(result["historical_delisted_universe_pit_verified"])
        self.assertFalse(result["corporate_actions_or_adjusted_prices_verified"])
        self.assertFalse(result["industry_15_or_other_v3_trade_score_promoted"])
        self.assertFalse(result["public_issuer_prices_or_individual_returns_written"])
        self.assertEqual(result["provider_data_new_calls"],0)
        forbidden=("KOSPI:","KOSDAQ:","TDD_CLSPRC","ACC_TRDVOL","private_repo_token")
        self.assertFalse(any(x in json.dumps(result) for x in forbidden))

    def test_unbridged_or_unrolled_progress_is_missing_not_zero_score(self):
        fake=fixture()
        fake.entries.pop("derived/boundaries-checkpoint.json")
        fake.entries.pop("derived/rolling/20/checkpoint.json")
        fake.entries.pop("derived/quality/incremental-checkpoint.json")
        report=run(fake)
        self.assertEqual(report["cross_cohort_bridges_completed"],0)
        self.assertEqual(report["rolling_return_horizon_research"]["20"]["complete_cohort_windows"],0)
        self.assertEqual(report["price_discontinuity_heuristic_cohorts_reviewed"],0)
        self.assertFalse(report["official_exchange_historical_factor_certified"])

    def test_checkpoint_cannot_claim_more_bridges_than_source(self):
        fake=fixture()
        fake.entries["derived/boundaries-checkpoint.json"]=saved_bridge(14)
        with self.assertRaises(PrivateProgressError):
            run(fake)

    def test_checkpoint_cannot_claim_more_windows_than_links(self):
        fake=fixture()
        row=json.loads(fake.entries["derived/rolling/20/checkpoint.json"])
        row["next_end_bucket"]=20
        fake.entries["derived/rolling/20/checkpoint.json"]=json.dumps(row).encode("utf8")
        with self.assertRaises(PrivateProgressError):
            run(fake)

    def test_private_tail_sha_must_be_valid(self):
        fake=fixture()
        fake.entries[source_path(DAYS[55:60])]=b"unverified-price-factor"
        with self.assertRaises(PrivateProgressError):
            run(fake)

    def test_mid_read_private_cursor_change_cannot_publish(self):
        fake=fixture()
        fake.replace_source_on_second_read=True
        with self.assertRaises(PrivateProgressError):
            run(fake)

    def test_private_repo_required_even_with_synthetic_checkpoints(self):
        fake=fixture()
        fake.repository="ykvic1/korean-stock-screener"
        with self.assertRaises(PrivateProgressError):
            run(fake)


    def test_successful_private_rolling_research_refreshes_only_public_aggregates(self):
        flow=(Path(__file__).resolve().parents[1]/
              ".github/workflows/v3-krx-public-derived-progress.yml").read_text(
                  encoding="utf8")
        self.assertIn("workflow_run:",flow)
        self.assertIn("V3 KRX historical derived-only backfill",flow)
        self.assertIn(
            "V3 KRX Private Derived-Only 20/60/120/250-Session Return Research",flow)
        self.assertIn(
            "V3 KRX Private Derived Corporate Action Risk Review (incremental)",flow)
        self.assertIn("types: [completed]",flow)
        self.assertIn("workflow_run.conclusion == 'success'",flow)
        self.assertIn("workflow_run.head_branch == 'main'",flow)
        self.assertIn("workflow_run.head_repository.full_name == github.repository",flow)
        self.assertIn("docs/v3-krx-derived-backfill-progress.json",flow)
        self.assertNotIn("KRX_API_KEY:",flow)


if __name__=="__main__":
    unittest.main()
