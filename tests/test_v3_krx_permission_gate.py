"""Offline KRX legal-scope guardrails. No KRX provider calls or private writes."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date
from io import StringIO
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_krx_permission_gate as p
import v3_krx_private_github_backfill as archive


NOW=date(2026,10,20)
VALID={
    "V3_KRX_PRIVATE_STORAGE_AUTHORIZED":"true",
    "V3_KRX_CLOUD_STORAGE_WRITTEN_APPROVED":"true",
    "V3_KRX_STORAGE_SCOPE":"private-github-noncommercial-research",
    "V3_KRX_APPROVAL_REFERENCE":"KRX-WRITTEN-SYNTHETIC-NOT-REAL",
    "V3_KRX_LICENSE_RECHECK_BY":"2027-01-18",
}


class LicenseGuardTests(unittest.TestCase):
    def test_operator_written_scope_reference_recheck_never_certifies_krx(self):
        status=p.confirm_operator_approval(VALID,today=NOW)
        self.assertTrue(status["operator_approval_attestation_present"])
        self.assertFalse(status["official_written_permission_independently_authenticated"])
        self.assertFalse(status["commercial_distribution_authorized"])
        self.assertFalse(status["public_source_distribution_authorized"])
        self.assertNotIn(VALID["V3_KRX_APPROVAL_REFERENCE"],str(status))
        self.assertEqual(status["recheck_by"],"2027-01-18")

    def test_boolean_alone_is_not_permission_to_archive_krx(self):
        for key in VALID:
            without=dict(VALID)
            without.pop(key)
            with self.subTest(key=key),self.assertRaises(p.PermissionGateError):
                p.confirm_operator_approval(without,today=NOW)
        no_document=dict(VALID)
        no_document["V3_KRX_APPROVAL_REFERENCE"]="approved"
        with self.assertRaisesRegex(p.PermissionGateError,"written_krx_permission_reference_required"):
            p.confirm_operator_approval(no_document,today=NOW)
        public=dict(VALID)
        public["V3_KRX_STORAGE_SCOPE"]="public-stock-prices"
        with self.assertRaises(p.PermissionGateError):
            p.confirm_operator_approval(public,today=NOW)

    def test_expired_invalid_or_excessively_long_review_is_denied(self):
        for review in ("2026-10-19","2027-01-19","2026-13-15","2026-10-2",
                       "9999-99-99",True,None):
            sample=dict(VALID)
            sample["V3_KRX_LICENSE_RECHECK_BY"]=review
            with self.subTest(review=review),self.assertRaises(p.PermissionGateError):
                p.confirm_operator_approval(sample,today=NOW)

    def test_default_false_means_cli_dryrun_and_live_no_requests(self):
        captured=StringIO()
        with patch.dict(os.environ,{
            "V3_KRX_PRIVATE_STORAGE_AUTHORIZED":"true",
            "V3_PRIVATE_ARCHIVE_REPO":"ykvic1/korean-stock-private-data",
            "V3_PRIVATE_ARCHIVE_TOKEN":"FAKE-synthetic-not-real-security-token",
            "KRX_API_KEY":"FAKE-krx-provider-key"},clear=True):
            with patch("sys.argv",["v3_krx_private_github_backfill.py","--live"]):
                with redirect_stdout(captured):
                    self.assertEqual(archive.main(),0)
        result=json.loads(captured.getvalue())
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(result["provider_calls"],0)
        self.assertEqual(result["private_archive_files_written"],0)

    def test_scheduled_job_requires_multiple_distinct_official_permission_variables(self):
        wf=Path(".github/workflows/v3-krx-private-historical-backfill.yml").read_text()
        source=Path("v3_krx_private_github_backfill.py").read_text()
        self.assertIn("vars.V3_KRX_CLOUD_STORAGE_WRITTEN_APPROVED == 'true'",wf)
        self.assertIn("vars.V3_KRX_APPROVAL_REFERENCE != ''",wf)
        self.assertIn("vars.V3_KRX_LICENSE_RECHECK_BY != ''",wf)
        self.assertIn("V3_KRX_STORAGE_SCOPE:",wf)
        self.assertIn("confirm_operator_approval(os.environ)",source)
        self.assertIn('"provider_calls":0',source)
        self.assertNotIn("contents: write",wf)
        self.assertIn("default: dry_run",wf)


if __name__=="__main__":
    unittest.main()
