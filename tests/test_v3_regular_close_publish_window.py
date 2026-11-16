"""Official publish boundaries must preserve the last valid indicator files."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import v3_regular_close_indicators as history


class RegularClosePublicationWindowTest(unittest.TestCase):
    def test_friday_waits_through_weekend_without_private_calls_or_public_writes(self):
        publish = datetime(2026, 11, 15, 23, tzinfo=timezone.utc)
        original_cwd = Path.cwd()
        with TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                docs = Path('docs')
                docs.mkdir()
                (docs / 'v22-market-catalog.json').write_text(
                    json.dumps({'date': '2026-11-13', 'rows': []}))
                output = docs / history.OUTPUT
                progress = docs / 'v3-regular-close-history-progress.json'
                output.write_text('{}\n')
                progress.write_text('{"previous_valid":true}\n')
                original = (output.read_bytes(), progress.read_bytes())
                with patch.object(history, 'datetime') as clock, \
                     patch.object(history, 'PrivateGitStorage') as storage, \
                     patch.object(history, 'collect') as collect, \
                     patch.object(history, 'history_dates', return_value=[]), \
                     patch.dict(os.environ, {'V3_PRIVATE_ARCHIVE_REPO': ''}), \
                     patch('builtins.print') as printed:
                    for now in (
                        datetime(2026, 11, 13, 20, 5, tzinfo=timezone.utc),
                        datetime(2026, 11, 15, 22, 59, 59, tzinfo=timezone.utc),
                    ):
                        clock.now.return_value = now
                        history.main()
                        status = json.loads(printed.call_args.args[0])
                        self.assertEqual(status['status'], 'waiting_for_official_publish_window')
                        self.assertEqual(status['official_publish_candidate_utc'], publish.isoformat())
                        self.assertEqual(status['source_requests'], 0)
                        self.assertFalse(status['published_indicators_changed'])
                    storage.assert_not_called()
                    collect.assert_not_called()
                    self.assertEqual((output.read_bytes(), progress.read_bytes()), original)
                    # At the boundary, normal destination validation becomes reachable.
                    clock.now.return_value = publish
                    with self.assertRaisesRegex(
                            history.CloseHistoryError, '^private_regular_close_destination_unavailable$'):
                        history.main()
                    storage.assert_not_called()
                    self.assertEqual((output.read_bytes(), progress.read_bytes()), original)
            finally:
                os.chdir(original_cwd)
