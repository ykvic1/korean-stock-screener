"""Current five-area observed score -> frozen, PIT-gated research evaluation.

No legacy industry/IAS weights, partial-score rescaling, API calls or release.
A digest binds the saved payload but cannot prove historical source authenticity.
"""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from v3_frozen_score_backtest import scored_walkforward, ScoredBacktestError, _date, _instant, KST

WEIGHTS = {"volume":20, "technical":25, "financial":20, "valuation":20, "momentum":15}
SCHEMA = "v3-five-area-frozen-observed-score-0.1"


def _number(value):
    if type(value) not in (int, float):
        raise ScoredBacktestError("invalid_five_area_numeric_value")
    try:
        n = Decimal(str(value))
        if not n.is_finite():
            raise ValueError()
        return n
    except (InvalidOperation, ValueError):
        raise ScoredBacktestError("invalid_five_area_numeric_value") from None


def snapshot_digest(snapshot):
    payload = {k:v for k,v in snapshot.items() if k != "snapshot_sha256"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def freeze_observed_row(row, *, issuer_id, frozen_at_utc):
    """Freeze an actual generate() scorecard row; caller stores it privately.

    Only full source coverage can enter full-model score/return correlation.
    Partial rows remain valid displayed observations but are excluded here.
    Never backdate frozen_at_utc to claim today's data existed in the past.
    """
    if type(row) is not dict or row.get("certified") is not False or row.get("official_score") is not None:
        raise ScoredBacktestError("untrusted_five_area_observed_row")
    if (type(row.get("code")) is not str or not re.fullmatch(r"[0-9]{6}", row["code"])
        or type(issuer_id) is not str or not re.fullmatch(r"[0-9]{8}", issuer_id)
        or row.get("market") not in ("KOSPI", "KOSDAQ")):
        raise ScoredBacktestError("invalid_five_area_issuer_identity")
    day = _date(row.get("asof"))
    when = _instant(frozen_at_utc).astimezone(KST)
    if when.date() != day or (when.hour, when.minute) < (15,30):
        raise ScoredBacktestError("five_area_freeze_outside_signal_close")
    points, coverage = row.get("factors"), row.get("factor_available")
    if (type(points) is not dict or set(points) != set(WEIGHTS)
        or type(coverage) is not dict or set(coverage) != set(WEIGHTS)
        or any(type(coverage[k]) is not int or coverage[k] != v for k,v in WEIGHTS.items())
        or type(row.get("available")) is not int or row["available"] != 100
        or type(row.get("unavailable")) is not int or row["unavailable"] != 0):
        raise ScoredBacktestError("incomplete_or_legacy_five_area_score")
    for key,maximum in WEIGHTS.items():
        if not 0 <= _number(points[key]) <= maximum:
            raise ScoredBacktestError("five_area_points_exceed_budget")
    total = _number(row.get("earned"))
    # Published scorecard total is deliberately rounded to four decimals.
    if not 0 <= total <= 100 or abs(total-sum(_number(v) for v in points.values())) > Decimal("0.00005"):
        raise ScoredBacktestError("five_area_total_does_not_reconcile")
    out = {"schema":SCHEMA, "code":row["code"], "issuer_id":issuer_id,
        "market":row["market"], "asof":row["asof"],
        "frozen_at_utc":frozen_at_utc, "weights":dict(WEIGHTS),
        "factors":deepcopy(points), "factor_available":dict(coverage),
        "candidate_full_total_research_only":row["earned"],
        "confirmed_score":None, "confirmed_buy":False,
        "historical_source_authenticated":False}
    out["snapshot_sha256"] = snapshot_digest(out)
    return out


def five_area_scored_walkforward(payloads, **options):
    """Reuse loss-first PIT/event gates after authenticating payload consistency."""
    if type(payloads) is not list or len(payloads) > 120:
        raise ScoredBacktestError("invalid_historical_scored_cohort")
    for p in payloads:
        s = p.get("frozen_score_snapshot") if type(p) is dict else None
        if type(s) is not dict or s.get("schema") != SCHEMA:
            raise ScoredBacktestError("missing_five_area_frozen_contract")
        row = {"code":s.get("code"), "market":s.get("market"), "asof":s.get("asof"),
            "factors":s.get("factors"), "factor_available":s.get("factor_available"),
            "earned":s.get("candidate_full_total_research_only"),
            "available":100,"unavailable":0,"certified":False,"official_score":None}
        expected = freeze_observed_row(row, issuer_id=s.get("issuer_id"),
                                       frozen_at_utc=s.get("frozen_at_utc"))
        if s != expected:
            raise ScoredBacktestError("five_area_snapshot_payload_mismatch")
    result = scored_walkforward(payloads, **options)
    result.update({"schema":"v3-five-area-frozen-quality-walkforward-0.1",
        "score_model_weights":dict(WEIGHTS), "industry_in_score":False,
        "partial_score_rescaled":False,"snapshot_payload_digests_recomputed":True})
    return result
