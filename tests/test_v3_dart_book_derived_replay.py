"""Offline annual-book race replay regression; zero API and no source rewrites."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import v3_dart_bulk_history as bulk
from test_v3_dart_rolling_book_reference import DAY, fixture


def archive_fixture():
    market, catalog = fixture()
    _, _, _, fiscal, cohorts = bulk.plan(market, catalog)
    current = {code: {str(year): {} for year in fiscal}
               for cohort in cohorts for code in cohort}
    archive = bulk.combine(market, catalog, None, current)
    annual = {"asof": DAY, "market_listed_total": len(catalog["rows"]),
              "records": [], "observations": []}
    return market, catalog, archive, annual


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")


class DerivedAnnualBookReplayTest(TestCase):
    def inputs(self, folder, *, invalid_archive=False, book_day=None):
        market, catalog, archive, annual = archive_fixture()
        if invalid_archive:
            archive["historical_PIT_source_certified"] = True
        if book_day is not None:
            annual["asof"] = book_day
        paths = [Path(folder, file) for file in
                 ("market.json", "catalog.json", "annual.json",
                  "bulk.json", "scores.json")]
        for path, source in zip(paths, (market, catalog, annual, archive)):
            save(path, source)
        args = [flag for pair in zip(
            ("--market", "--catalog", "--annual", "--state", "--scores"),
            map(str, paths)) for flag in pair]
        return paths, args

    def test_replays_and_verifies_with_no_provider_calls_or_source_writes(self):
        with TemporaryDirectory() as tmp:
            paths, args = self.inputs(tmp)
            immutable = paths[3].read_bytes()
            with patch.object(bulk, "collect", side_effect=AssertionError("network")):
                bulk.main(args + ["--mode", "replay"])
                bulk.main(args + ["--mode", "verify"])
            self.assertEqual(paths[3].read_bytes(), immutable)
            score = json.loads(paths[4].read_text(encoding="utf8"))
            self.assertEqual(score, bulk.research_parts(
                json.loads(paths[3].read_text(encoding="utf8")),
                json.loads(paths[1].read_text(encoding="utf8")),
                json.loads(paths[2].read_text(encoding="utf8"))))
            self.assertFalse(score["historical_PIT_certified"] if
                             "historical_PIT_certified" in score else False)

    def test_mismatched_annual_book_rejected_before_score_write(self):
        with TemporaryDirectory() as tmp:
            paths, args = self.inputs(tmp, book_day="2026-01-01")
            with self.assertRaisesRegex(bulk.BulkHistoryError,
                                        "incompatible_bulk_scoring_asof"):
                bulk.main(args + ["--mode", "replay"])
            self.assertFalse(paths[4].exists())

    def test_source_provenance_change_rejected_without_overwrite(self):
        with TemporaryDirectory() as tmp:
            paths, args = self.inputs(tmp, invalid_archive=True)
            with self.assertRaisesRegex(bulk.BulkHistoryError,
                                        "replay_bulk_source_day_or_provenance_invalid"):
                bulk.main(args + ["--mode", "replay"])
            self.assertFalse(paths[4].exists())

    def test_missing_source_archive_rejected(self):
        with TemporaryDirectory() as tmp:
            paths, args = self.inputs(tmp)
            paths[3].unlink()
            with self.assertRaisesRegex(bulk.BulkHistoryError,
                                        "replay_bulk_source_day_or_provenance_invalid"):
                bulk.main(args + ["--mode", "replay"])
            self.assertFalse(paths[4].exists())
