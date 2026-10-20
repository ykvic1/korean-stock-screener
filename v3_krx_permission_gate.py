"""Fail-closed compliance check for licensed private KRX archive retention.

KRX OPEN API terms restrict commercial use and sharing source data with third
parties. A private GitHub repository does NOT by itself prove that third-party
cloud retention is permitted. This code checks an operator's documented,
time-bounded attestation; it cannot authenticate KRX's written reply.
"""
from __future__ import annotations

from datetime import date, timedelta
import re


class PermissionGateError(ValueError):
    """Fixed validation reasons without license-document or secret disclosure."""


def confirm_operator_approval(environment, *, today=None):
    """Require a specific written KRX retention review, never just a toggle.

    A reference and review date are operator-supplied audit context, NOT an
    automatic certification that KRX grants the requested rights.
    """
    current = today or date.today()
    if type(current) is not date:
        raise PermissionGateError("invalid_current_day")
    if environment.get("V3_KRX_PRIVATE_STORAGE_AUTHORIZED") != "true":
        raise PermissionGateError("archive_explicit_user_authorization_required")
    if environment.get("V3_KRX_CLOUD_STORAGE_WRITTEN_APPROVED") != "true":
        raise PermissionGateError("official_written_krx_cloud_retention_not_confirmed")
    if environment.get("V3_KRX_STORAGE_SCOPE") != "private-github-noncommercial-research":
        raise PermissionGateError("krx_approved_storage_scope_not_confirmed")
    ref = environment.get("V3_KRX_APPROVAL_REFERENCE")
    if (type(ref) is not str or not 12 <= len(ref) <= 128 or
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{11,127}", ref)
        or re.search(r"[0-9]{4}", ref) is None):
        raise PermissionGateError("written_krx_permission_reference_required")
    review_day = environment.get("V3_KRX_LICENSE_RECHECK_BY")
    if type(review_day) is not str:
        raise PermissionGateError("krx_license_review_date_required")
    try:
        checked = date.fromisoformat(review_day)
    except ValueError:
        raise PermissionGateError("invalid_krx_license_review_day") from None
    if checked.isoformat() != review_day or not current <= checked <= current + timedelta(days=90):
        raise PermissionGateError("krx_license_expired_or_review_window_invalid")
    return {
        "operator_approval_attestation_present": True,
        "official_written_permission_independently_authenticated": False,
        "retention_scope": "private-github-noncommercial-research",
        "recheck_by": checked.isoformat(),
        "permission_reference_recorded_without_logging": True,
        "commercial_distribution_authorized": False,
        "public_source_distribution_authorized": False,
    }
