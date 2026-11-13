"""Offline R1e evidence contract regression; no provider access."""
from copy import deepcopy
import unittest

from v3_frozen_score_backtest import ScoredBacktestError
from v3_r1e_frozen_evidence import (
    R1E_WEIGHTS, R1E_SCHEMA, freeze_r1e_observed_row,
    validate_r1e_snapshot, r1e_snapshot_digest,
)


def sample():
    return {
        "model_version": "v3-stable-r1e-operational-research",
        "code": "0001A0", "market": "KOSPI", "asof": "2026-11-12",
        "certified": False, "official_score": None,
        "available": 100.0, "unavailable": 0,
        "factors": {"volume": 15, "technical": 20, "financial": 8,
                    "valuation": 7.5, "momentum": 12},
        "factor_available": dict(R1E_WEIGHTS), "earned": 62.5,
    }


def freeze(row=None):
    return freeze_r1e_observed_row(
        sample() if row is None else row,
        issuer_id="00000001", frozen_at_utc="2026-11-12T07:00:00Z")


class R1eFrozenEvidenceTests(unittest.TestCase):
    def test_mixed_code_fractional_budget_and_uncertified_contract(self):
        snapshot = freeze()
        self.assertEqual(snapshot["schema"], R1E_SCHEMA)
        self.assertEqual(snapshot["factor_available"]["financial"], 12.5)
        self.assertFalse(snapshot["historical_source_authenticated"])
        self.assertIsNone(snapshot["confirmed_score"])
        self.assertTrue(validate_r1e_snapshot(snapshot))

    def test_legacy_weights_and_partial_evidence_rejected(self):
        for mutate in (
            lambda r: r["factor_available"].update(financial=12),
            lambda r: r["factor_available"].update(volume=20),
            lambda r: r.update(available=95),
            lambda r: r["factors"].update(volume=26),
            lambda r: r["factors"].update(technical=float("nan")),
            lambda r: r.update(earned=100),
            lambda r: r.update(certified=True),
            lambda r: r.update(model_version="v3-stable-r1d-operational-research"),
            lambda r: r.update(code="0001a0"),
            lambda r: r.update(code="0001A01"),
        ):
            row = sample()
            mutate(row)
            with self.subTest(row=row), self.assertRaises(ScoredBacktestError):
                freeze(row)

    def test_tamper_and_forged_digest_rejected(self):
        snapshot = freeze()
        for mutate in (
            lambda s: s["factors"].update(volume=16),
            lambda s: s.update(confirmed_buy=True),
            lambda s: s.update(historical_source_authenticated=True),
            lambda s: s.update(schema="v3-five-area-frozen-observed-score-0.1"),
        ):
            altered = deepcopy(snapshot)
            mutate(altered)
            altered["snapshot_sha256"] = r1e_snapshot_digest(altered)
            with self.assertRaises(ScoredBacktestError):
                validate_r1e_snapshot(altered)

    def test_freeze_requires_same_day_after_close(self):
        for stamp in ("2026-11-12T06:00:00Z", "2026-11-13T07:00:00Z"):
            with self.assertRaises(ScoredBacktestError):
                freeze_r1e_observed_row(
                    sample(), issuer_id="00000001", frozen_at_utc=stamp)


if __name__ == "__main__":
    unittest.main()
