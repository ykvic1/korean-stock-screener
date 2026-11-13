"""Exact saved-source work queues for the optional 90-first manual dispatcher.

The score-gap work families are mathematical ceilings, NOT source call plans.
A verified zero queue disables redundant dispatch for that collector only.
Unknown/stale/malformed source state never becomes a fabricated zero and leaves
the existing source collector responsible for strict validation. No API calls.
"""
from __future__ import annotations

import json
from pathlib import Path

from v3_dart_revenue_2022 import plan as revenue_plan
from v3_owner_profit_completion import extend as profit_completion


def _load(root, name):
    return json.loads((Path(root) / name).read_text(encoding="utf8"))


def source_queue_counts(root, catalog, operational):
    """Return exact pending work counts or None (not independently proven).

    This is an optimization only. The underlying collectors keep all receipt,
    fiscal-year, issuer, same-day and provider-budget gates.
    """
    result = {"annual": None, "profit": None}
    day = catalog.get("date")
    if (type(day) is not str or operational.get("asof") != day or
            catalog.get("listed_total") != operational.get("listed_total")):
        return result
    try:
        history = _load(root, "v3-dart-bulk-history.json")
        prior = _load(root, "v3-dart-revenue-2022-supplement.json")
        _eligible, _retained, selected = revenue_plan(catalog, history, prior)
        result["annual"] = len(selected)
    except (OSError, ValueError, TypeError, KeyError):
        # No source-eligibility assertion; do not turn exceptions into zero.
        pass
    try:
        book = _load(root, "v3-dart-book-reference.json")
        priority = _load(root, "v3-near-100-evidence-priority.json")
        prior = _load(root, "v3-dart-owner-profit-supplement.json")
        comparison = _load(root, "v3-operational-research-comparison.json")
        eps = _load(root, "v3-dart-reported-eps.json")
        _updated, _observed, _preferred, report = profit_completion(
            catalog, book, priority, prior, operational, comparison, eps)
        if (report.get("status") == "all_listed_profit_source_queue" and
                type(report.get("pending_all_listed_issuers")) is int and
                report["pending_all_listed_issuers"] >= 0):
            result["profit"] = report["pending_all_listed_issuers"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result


def should_dispatch(conditional_upper_bound, source_pending):
    """Unknown = preserve existing attempt; verified empty = no re-request."""
    if type(conditional_upper_bound) is not int or conditional_upper_bound < 0:
        raise ValueError("invalid_source_family_upper_bound")
    if source_pending is not None and (
            type(source_pending) is not int or source_pending < 0):
        raise ValueError("invalid_verified_source_queue_count")
    return conditional_upper_bound > 0 and source_pending != 0
