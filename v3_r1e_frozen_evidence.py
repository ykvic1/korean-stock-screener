"""R1e five-area research-only frozen evidence contract.

Independent of the legacy 20/25/20/20/15 frozen snapshot schema.
A digest verifies payload integrity, NOT historical source authenticity.
No network calls, score rescaling, official certification or return claims.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from v3_frozen_score_backtest import ScoredBacktestError, _date, _instant, KST

R1E_WEIGHTS = {"volume": 25, "technical": 30, "financial": 12.5,
               "valuation": 12.5, "momentum": 20}
R1E_SCHEMA = "v3-r1e-five-area-frozen-observed-score-0.1"


def _decimal(value):
    if type(value) not in (int, float):
        raise ScoredBacktestError("invalid_r1e_numeric")
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation()
        return number
    except (InvalidOperation, ValueError):
        raise ScoredBacktestError("invalid_r1e_numeric") from None


def r1e_snapshot_digest(snapshot):
    return hashlib.sha256(json.dumps(
        {k: v for k, v in snapshot.items() if k != "snapshot_sha256"},
        sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def freeze_r1e_observed_row(row, *, issuer_id, frozen_at_utc):
    """Freeze a full-evidence R1e row; caller must preserve authentic timing.

    This function cannot prove when source data was first available.
    """
    if (type(row) is not dict or row.get("certified") is not False
            or row.get("official_score") is not None
            or row.get("model_version") != "v3-stable-r1e-operational-research"):
        raise ScoredBacktestError("untrusted_r1e_observed_row")
    if (type(row.get("code")) is not str
            or not re.fullmatch(r"[0-9A-Z]{6}", row["code"])
            or type(issuer_id) is not str
            or not re.fullmatch(r"[0-9]{8}", issuer_id)
            or row.get("market") not in ("KOSPI", "KOSDAQ")):
        raise ScoredBacktestError("invalid_r1e_issuer_identity")
    day = _date(row.get("asof"))
    when = _instant(frozen_at_utc).astimezone(KST)
    if when.date() != day or (when.hour, when.minute) < (15, 30):
        raise ScoredBacktestError("r1e_freeze_outside_signal_close")
    factors, coverage = row.get("factors"), row.get("factor_available")
    if (type(factors) is not dict or set(factors) != set(R1E_WEIGHTS)
            or type(coverage) is not dict or set(coverage) != set(R1E_WEIGHTS)
            or _decimal(row.get("available")) != 100
            or _decimal(row.get("unavailable")) != 0):
        raise ScoredBacktestError("incomplete_r1e_observation")
    for key, budget in R1E_WEIGHTS.items():
        if _decimal(coverage[key]) != _decimal(budget):
            raise ScoredBacktestError("incomplete_r1e_observation")
        if not 0 <= _decimal(factors[key]) <= _decimal(budget):
            raise ScoredBacktestError("r1e_points_exceed_budget")
    earned = _decimal(row.get("earned"))
    if not 0 <= earned <= 100 or abs(
        earned - sum((_decimal(v) for v in factors.values()), Decimal(0))
    ) > Decimal("0.00005"):
        raise ScoredBacktestError("r1e_total_does_not_reconcile")
    snapshot = {
        "schema": R1E_SCHEMA, "model_version": row["model_version"],
        "code": row["code"], "issuer_id": issuer_id, "market": row["market"],
        "asof": row["asof"], "frozen_at_utc": frozen_at_utc,
        "weights": dict(R1E_WEIGHTS), "factors": deepcopy(factors),
        "factor_available": deepcopy(coverage),
        "candidate_full_total_research_only": row["earned"],
        "confirmed_score": None, "confirmed_buy": False,
        "historical_source_authenticated": False,
    }
    snapshot["snapshot_sha256"] = r1e_snapshot_digest(snapshot)
    return snapshot


def validate_r1e_snapshot(snapshot):
    """Fail closed on altered snapshots, including valid-looking forged digests."""
    if type(snapshot) is not dict or snapshot.get("schema") != R1E_SCHEMA:
        raise ScoredBacktestError("missing_r1e_frozen_contract")
    row = {
        "model_version": snapshot.get("model_version"),
        "code": snapshot.get("code"), "market": snapshot.get("market"),
        "asof": snapshot.get("asof"), "factors": snapshot.get("factors"),
        "factor_available": snapshot.get("factor_available"),
        "earned": snapshot.get("candidate_full_total_research_only"),
        "available": 100, "unavailable": 0,
        "certified": False, "official_score": None,
    }
    expected = freeze_r1e_observed_row(
        row, issuer_id=snapshot.get("issuer_id"),
        frozen_at_utc=snapshot.get("frozen_at_utc"))
    if snapshot != expected:
        raise ScoredBacktestError("r1e_snapshot_payload_mismatch")
    return True
