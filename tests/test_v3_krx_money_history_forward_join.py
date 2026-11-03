"""No-network replay of real-schema historical and forward money boundaries."""
import copy
import gzip
from hashlib import sha256
import json
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from test_v3_krx_money_forward import weekdays
from test_v3_krx_money_horizon_coverage import fixture
from v3_krx_money_backfill import CHECKPOINT, _path, _cohort_at_recent_first
from v3_krx_money_forward import CHECKPOINT as FORWARD, SCHEMA, _prefix
from v3_krx_money_horizon_coverage import collect, MoneyCoverageError


def joined_fixture(forward_completed=2):
    storage,old_dates,digest=fixture()
    old=[json.loads(gzip.decompress(storage.files[_path(
        _cohort_at_recent_first(old_dates,i))])) for i in range(8)]
    state=json.loads(storage.files[CHECKPOINT])
    storage.files={}
    dates=weekdays("2026-08-20",42)  # 8 full blocks + Oct 15/16 residue
    assert dates[-1]=="2026-10-16"
    for i,data in enumerate(old):
        window=_cohort_at_recent_first(dates,i)
        data.update(window_start=window[0],window_end=window[-1])
        storage.files[_path(window)]=gzip.compress(json.dumps(data).encode())
    state["last_window_file_sha256"]=sha256(storage.files[_path(
        _cohort_at_recent_first(dates,7))]).hexdigest()
    storage.files[CHECKPOINT]=json.dumps(state).encode()
    forward=weekdays("2026-10-15",14)  # two full blocks plus four open sessions
    for i in range(2):
        window=forward[5*i:5*(i+1)]
        data=copy.deepcopy(old[0])
        data.update(window_start=window[0],window_end=window[-1])
        storage.files[_path(window)]=gzip.compress(json.dumps(data).encode())
    tail=forward[5*(forward_completed-1):5*forward_completed]
    fstate={"schema":SCHEMA,"historical_anchor_end":"2026-10-16",
            "next_window_index":forward_completed,
            "completed_candidate_prefix_sha256":_prefix(forward,forward_completed),
            "issuer_cohort_records":3*forward_completed,"compressed_factor_bytes":1000,
            "last_window_file_sha256":sha256(storage.files[_path(tail)]).hexdigest(),
            "raw_source_file_count":0}
    storage.files[FORWARD]=json.dumps(fstate).encode()
    return storage,dates,digest,forward


class JoinedMoneyHistoryTests(unittest.TestCase):
    def test_combined_read_budget_includes_forward_blocks(self):
        storage,dates,digest,forward=joined_fixture()
        with patch("v3_krx_money_horizon_coverage.MAX_COHORTS",8):
            result=collect(storage,dates,digest,forward_dates=forward)
        self.assertEqual(result["total_complete_cohorts_inspected"],8)
        self.assertEqual(result["historical_cohorts_inspected"],6)
        self.assertEqual(result["forward_complete_cohorts_inspected"],2)

    def test_cli_refuses_to_replace_newer_analysis_with_older_endpoint(self):
        from v3_krx_money_horizon_coverage import main, OUTPUT
        storage,dates,digest,forward=joined_fixture()
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/OUTPUT
            target.write_text(json.dumps({"analysis_complete_cohort_endpoint":"2026-10-28"}))
            before=target.read_bytes()
            with patch.dict("os.environ",{
                    "V3_PRIVATE_ARCHIVE_REPO":storage.repository,
                    "V3_PRIVATE_ARCHIVE_TOKEN":"synthetic-no-credential-000000"}), patch(
                    "v3_krx_money_horizon_coverage.history_candidates",return_value=(dates,digest)), patch(
                    "v3_krx_money_horizon_coverage.forward_candidates",return_value=forward), patch(
                    "v3_krx_money_horizon_coverage.collect",
                    return_value={"analysis_complete_cohort_endpoint":"2026-10-21"}):
                with self.assertRaisesRegex(MoneyCoverageError,"endpoint_regressed"):
                    main(["--docs",directory,"--mode","write"])
            self.assertEqual(target.read_bytes(),before)

    def test_forward_blocks_advance_endpoint_and_keep_partial_tail_out(self):
        storage,dates,digest,forward=joined_fixture()
        old_bytes=storage.files[CHECKPOINT]
        result=collect(storage,dates,digest,forward_dates=forward)
        self.assertEqual(result["historical_archive_endpoint"],"2026-10-14")
        self.assertEqual(result["analysis_complete_cohort_endpoint"],"2026-10-28")
        self.assertEqual(result["forward_complete_cohorts_inspected"],2)
        self.assertEqual(result["total_complete_cohorts_inspected"],10)
        self.assertEqual(result["forward_open_tail_candidate_sessions"],4)
        self.assertEqual(result["forward_candidate_sessions_not_in_analysis"],4)
        self.assertTrue(result["historical_forward_boundary_verified"])
        self.assertFalse(result["current_market_session_alignment_verified"])
        self.assertFalse(result["scoring_allowed"])
        self.assertEqual(storage.files[CHECKPOINT],old_bytes)

    def test_uncheckpointed_full_block_is_never_silently_imported(self):
        storage,dates,digest,forward=joined_fixture(1)
        result=collect(storage,dates,digest,forward_dates=forward)
        self.assertEqual(result["analysis_complete_cohort_endpoint"],"2026-10-21")
        self.assertEqual(result["forward_uncollected_complete_cohorts"],1)
        self.assertEqual(result["forward_candidate_sessions_not_in_analysis"],9)

    def test_changed_prefix_missing_bridge_and_tampered_tail_are_blocked(self):
        for fault in ("prefix","bridge","tail","residue"):
            storage,dates,digest,forward=joined_fixture()
            if fault=="prefix":
                state=json.loads(storage.files[FORWARD])
                state["completed_candidate_prefix_sha256"]="b"*64
                storage.files[FORWARD]=json.dumps(state).encode()
            elif fault=="bridge":
                del storage.files[_path(forward[:5])]
            elif fault=="tail":
                storage.files[_path(forward[5:10])]=b"changed"
            else:
                forward=forward[1:]
            with self.subTest(fault=fault),self.assertRaises(MoneyCoverageError):
                collect(storage,dates,digest,forward_dates=forward)

    def test_missing_forward_checkpoint_preserves_explicit_historical_basis(self):
        storage,dates,digest,forward=joined_fixture()
        del storage.files[FORWARD]
        result=collect(storage,dates,digest,forward_dates=forward)
        self.assertEqual(result["analysis_complete_cohort_endpoint"],"2026-10-14")
        self.assertFalse(result["forward_checkpoint_available"])
        self.assertEqual(result["forward_uncollected_complete_cohorts"],2)

    def test_forward_checkpoint_change_during_read_blocks_publication(self):
        storage,dates,digest,forward=joined_fixture()
        original=storage.get_content
        reads=0
        def get(path):
            nonlocal reads
            if path==FORWARD:
                reads+=1
                if reads==2:
                    return b"changed","new-version"
            return original(path)
        storage.get_content=get
        with self.assertRaisesRegex(MoneyCoverageError,"forward_checkpoint_changed"):
            collect(storage,dates,digest,forward_dates=forward)


if __name__=="__main__":
    unittest.main()
