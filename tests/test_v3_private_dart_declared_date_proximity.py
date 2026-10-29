"""Synthetic private DART planned-date x KRX heuristic-only reconciliation."""
import gzip
from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_private_dart_declared_date_proximity as p
import v3_private_dart_structured_action_receipts as principal
import v3_private_dart_structured_action_extension as extension
import v3_private_dart_krx_filing_title_triage as triage
from test_v3_krx_derived_5day import FakePrivate
from v3_private_dart_krx_filing_title_triage import SCHEMA as SHORTLIST_SCHEMA


RECEIPTS = [
    {"rcept_no": "20161101000001", "corp_code": "00121932",
     "title_hints": ["bonus_issue"]},
    {"rcept_no": "20161103000002", "corp_code": "00121932",
     "title_hints": ["merger_split"]},
]


def private_shortlist(*, duplicate=False):
    rows = []
    for record, filing, risk in zip(
            RECEIPTS, ("2016-11-01", "2016-11-03"),
            ("2016-10-31", "2016-11-02")):
        rows.append({
            "heuristic_issuer_market": "KOSPI:005000",
            "heuristic_first_day": "2016-10-26",
            "heuristic_last_day": risk,
            "heuristic_kind": "large_four_interval_bucket_change",
            "filing_receipt_no": record["rcept_no"],
            "filing_corp_code": record["corp_code"],
            "filing_stock_code": "005000",
            "filing_date": filing,
            "days_from_heuristic_end": (p._day(filing) - p._day(risk)).days,
            "title_keyword_hints": record["title_hints"],
            "event_authentication": "not_verified",
        })
    if duplicate:
        rows.append(dict(rows[0]))
    payload = {
        "schema": SHORTLIST_SCHEMA,
        "first_month": p.MONTHS[0],
        "second_month": p.MONTHS[1],
        "ticker_date_receipt_pairs": len(rows),
        "title_keyword_pairs": len(rows),
        "filing_title_or_candidate_is_event_proof": False,
        "scores_authorized": False,
        "candidates": rows,
    }
    return gzip.compress(principal.canonical(payload), mtime=0)


def principal_blob():
    results = [
        {"endpoint": ep, "provider_status": "013",
         "list_rows": 0, "exact_receipt_match": False,
         "reported_schedule_date_candidates": []}
        for ep in principal.ENDPOINTS
    ]
    results[2] = {
        "endpoint": "fricDecsn", "provider_status": "000",
        "list_rows": 1, "exact_receipt_match": True,
        "reported_schedule_date_candidates": [
            {"field": "nstk_asstd", "reported_date": "2016-11-03"},
            {"field": "nstk_lstprd", "reported_date": "2016-11-18"},
        ],
    }
    return principal.canonical(principal._report(0, RECEIPTS[0], results))


def supplement_blob():
    result = {
        "endpoint": "pifricDecsn", "provider_status": "000",
        "list_rows": 1, "exact_receipt_match": True,
        "reported_schedule_date_candidates": [
            {"field": "fric_nstk_asstd", "reported_date": "2016-12-20"},
        ],
    }
    return principal.canonical(extension._report(0, RECEIPTS[0], [result]))


class DateProximityTests(unittest.TestCase):
    def test_exact_receipt_dates_are_only_temporal_research(self):
        result = p.reconcile(
            private_shortlist(),
            {RECEIPTS[0]["rcept_no"]: principal_blob()},
            {RECEIPTS[0]["rcept_no"]: supplement_blob()}, pair_index=0)
        self.assertEqual(result["selected_unique_receipts"], 2)
        self.assertEqual(result["verified_private_structured_reports"], 2)
        self.assertEqual(result["not_yet_saved_report_slots"], 2)
        self.assertEqual(result["exact_receipt_category_matches"], 2)
        self.assertEqual(result["declared_schedule_date_fields"], 3)
        self.assertEqual(result["declared_dates_near_krx_risk_window"], 1)
        self.assertEqual(result["declared_dates_outside_krx_risk_window"], 2)
        self.assertTrue(result["zero_provider_calls"])
        for key in ("temporal_match_is_corporate_action_proof",
                    "date_nonmatch_is_absence_of_action_proof",
                    "official_actual_completion_verified",
                    "adjusted_prices_certified", "historical_pit_issuers_certified",
                    "private_identity_or_dates_published"):
            self.assertFalse(result[key])
        self.assertEqual(result["scores_promoted"], 0)
        text = json.dumps(result)
        self.assertNotIn(RECEIPTS[0]["rcept_no"], text)
        self.assertNotIn("00121932", text)
        self.assertNotIn("2016-11-03", text)

    def test_bounded_second_slice_can_ignore_first_receipt(self):
        result = p.reconcile(private_shortlist(), {}, {}, pair_index=0,
                             start=1, limit=1)
        self.assertEqual(result["selected_unique_receipts"], 1)
        self.assertEqual(result["not_yet_saved_report_slots"], 2)
        self.assertEqual(result["declared_schedule_date_fields"], 0)
        self.assertFalse(result["date_nonmatch_is_absence_of_action_proof"])

    def test_mismatched_report_or_unsafe_claim_fails_closed(self):
        base = private_shortlist()
        original = principal_blob()
        for mismatched in (
                {RECEIPTS[1]["rcept_no"]: original},
                {"20161101999999": original}):
            with self.assertRaises(p.ProximityError):
                p.reconcile(base, mismatched, {}, pair_index=0, limit=1)
        forged = json.loads(original)
        forged["scores_authorized"] = True
        with self.assertRaisesRegex(
                p.ProximityError, "invalid_private_proximity_structured_report"):
            p.reconcile(base, {RECEIPTS[0]["rcept_no"]:
                        principal.canonical(forged)}, {}, pair_index=0)
        forged = json.loads(original)
        forged["corp_code"] = "00000001"
        with self.assertRaises(p.ProximityError):
            p.reconcile(base, {RECEIPTS[0]["rcept_no"]:
                        principal.canonical(forged)}, {}, pair_index=0)

    def test_real_private_readonly_adapter_reads_only_saved_receipts(self):
        store = FakePrivate()
        blob = private_shortlist()
        store.blobs[triage.CHECKPOINT] = principal.canonical({
            **triage._empty(), "next_pair_index": 1,
            "last_file": triage._path(0),
            "last_digest": sha256(blob).hexdigest(),
        })
        store.blobs[triage._path(0)] = blob
        store.blobs[principal._path(0, RECEIPTS[0]["rcept_no"])] = principal_blob()
        store.blobs[extension._report_path(0, RECEIPTS[0]["rcept_no"])] = supplement_blob()
        original = dict(store.blobs)
        outcome = p.read_only_private_audit(store, pair_index=0)
        self.assertEqual(outcome["status"], "private_declared_date_temporal_research")
        self.assertEqual(outcome["source_receipts_total_in_pair"], 2)
        self.assertEqual(outcome["selected_slice_remaining_receipts"], 0)
        self.assertEqual(outcome["verified_private_structured_reports"], 2)
        self.assertEqual(outcome["declared_dates_near_krx_risk_window"], 1)
        self.assertEqual(outcome["provider_calls"], 0)
        self.assertEqual(outcome["private_files_written"], 0)
        self.assertEqual(store.posts, 0)
        self.assertEqual(store.blobs, original)
        self.assertNotIn(RECEIPTS[0]["rcept_no"], json.dumps(outcome))

        out = StringIO()
        with (patch.dict(p.os.environ, {
                    "V3_PRIVATE_ARCHIVE_REPO": p.PRIVATE_REPO,
                    "V3_PRIVATE_ARCHIVE_TOKEN": "t" * 40,
                    "V3_PROX_PAIR_INDEX": "0",
                    "V3_PROX_START": "0",
                    "V3_PROX_LIMIT": "8"}),
              patch.object(p, "PrivateGitStorage", return_value=store),
              redirect_stdout(out)):
            code = p.main()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["provider_calls"], 0)
        self.assertNotIn(RECEIPTS[0]["rcept_no"], out.getvalue())
        self.assertNotIn("00121932", out.getvalue())

    def test_private_runner_waits_or_rejects_changed_completed_source(self):
        store = FakePrivate()
        store.blobs[triage.CHECKPOINT] = principal.canonical(triage._empty())
        waiting = p.read_only_private_audit(store, pair_index=0)
        self.assertEqual(waiting["status"], "waiting_for_complete_private_title_shortlist")
        self.assertEqual(waiting["provider_calls"], 0)
        self.assertEqual(store.posts, 0)

        blob = private_shortlist()
        store.blobs[triage.CHECKPOINT] = principal.canonical({
            **triage._empty(), "next_pair_index": 1,
            "last_file": triage._path(0), "last_digest": "f" * 64,
        })
        store.blobs[triage._path(0)] = blob
        with self.assertRaisesRegex(
                p.ProximityError, "private_proximity_last_completed_pair_mutated"):
            p.read_only_private_audit(store, pair_index=0)
        self.assertEqual(store.posts, 0)

        store.repository = "ykvic1/korean-stock-screener"
        with self.assertRaisesRegex(
                p.ProximityError, "private_proximity_wrong_repository"):
            p.read_only_private_audit(store, pair_index=0)

    def test_manual_runner_workflow_never_has_schedule_or_source_credentials(self):
        workflow = Path(
            ".github/workflows/v3-private-dart-date-proximity.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("options: [dry_run, live]", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("V3_PRIVATE_ARCHIVE_TOKEN:", workflow)
        self.assertNotIn("DART_API_KEY:", workflow)
        self.assertNotIn("KRX_API_KEY:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("git push", workflow)

    def test_shortlist_duplicates_and_invalid_calendar_fail_closed(self):
        with self.assertRaisesRegex(
                p.ProximityError, "invalid_private_proximity_source_row"):
            p.reconcile(private_shortlist(duplicate=True), {}, {},
                        pair_index=0)
        with self.assertRaises(p.ProximityError):
            p.reconcile(b"not private gzip", {}, {}, pair_index=0)
        with self.assertRaises(p.ProximityError):
            p.reconcile(private_shortlist(), {}, {}, pair_index=0, limit=9)
        with self.assertRaises(p.ProximityError):
            p.reconcile(private_shortlist(), {}, {}, pair_index=0, start=3)


if __name__ == "__main__":
    unittest.main()
