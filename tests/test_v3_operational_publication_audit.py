"""Catch publication drift that an internally valid score JSON cannot catch."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from v3_operational_publication_audit import FILES, audit, failed_refresh_evidence

ROOT = Path(__file__).resolve().parents[1]


class FailedRefreshEvidenceTests(unittest.TestCase):
    def test_fixed_failure_is_bracketed_and_all_publication_blobs_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args, date=None):
                env = os.environ.copy()
                if date:
                    env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
                return subprocess.check_output(
                    ['git', '-c', 'user.name=Audit Test', '-c', 'user.email=audit@example.test',
                     *args], cwd=root, env=env, text=True).strip()

            git('init', '-q')
            docs = root / 'docs'
            docs.mkdir()
            for name in (*FILES, 'index.html', 'workspace.html'):
                (docs / name).write_text(name, encoding='utf-8')
            git('add', 'docs')
            git('commit', '-qm', 'Previous publication', date='2026-11-13T18:48:46Z')
            before = git('rev-parse', 'HEAD')
            (docs / 'unrelated.txt').write_text('another publication', encoding='utf-8')
            git('add', 'docs')
            git('commit', '-qm', 'Unrelated update', date='2026-11-13T18:57:17Z')
            incident = {'before_commit': before, 'after_commit': git('rev-parse', 'HEAD'),
                        'failed_at_utc': '2026-11-13T18:57:09+00:00'}
            result = failed_refresh_evidence(root, incident)
            self.assertEqual(result['status'], 'verified_preserved')
            self.assertEqual(result['matched_file_count'], 8)
            self.assertNotIn('docs/unrelated.txt', result['matched_git_blob_ids'])

            (docs / FILES[3]).write_text('unexpected score update', encoding='utf-8')
            git('add', 'docs')
            git('commit', '--amend', '-q', '--no-edit', date='2026-11-13T18:57:17Z')
            result = failed_refresh_evidence(root, {**incident, 'after_commit': git('rev-parse', 'HEAD')})
            self.assertEqual(result['status'], 'evidence_mismatch')
            self.assertEqual(result['matched_file_count'], 7)

            result = failed_refresh_evidence(root, {**incident, 'after_commit': 'a' * 40})
            self.assertEqual(result['status'], 'git_history_unavailable')


class OperationalPublicationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {n: (ROOT / 'docs' / n).read_bytes() for n in FILES}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.docs = Path(self.temp.name)
        for n, value in self.original.items():
            (self.docs / n).write_bytes(value)

    def mutate(self, name, change):
        p = self.docs / name
        data = json.loads(p.read_bytes())
        change(data)
        p.write_text(json.dumps(data, ensure_ascii=False))

    def test_actual_rollover_reconciles_without_promoting_certification(self):
        result = audit(self.docs)
        self.assertTrue(result['publication_coherent'])
        self.assertEqual(result['listed_total'], result['pages']['linked'])
        self.assertEqual(result['unpriced_count'], result['pages']['unpriced'])
        self.assertEqual(result['unexplained_unpriced_count'], 0)
        self.assertEqual(sum(result['available_weight_distribution'].values()), result['listed_total'])
        self.assertFalse(result['operational_acceptance_complete'])
        self.assertFalse(result['investment_score_certified'])
        self.assertEqual(result['provider_calls'], 0)
        self.assertIn('actual_failed_refresh_preserves_previous_publication',
                      result['remaining_acceptance_evidence'])

    def test_csv_cell_drift_fails_even_when_json_is_unchanged(self):
        p = self.docs / FILES[3]
        text = p.read_text(encoding='utf-8-sig')
        p.write_text(text.replace('v3-stable-r1e', 'v3-stable-stale', 1), encoding='utf-8-sig')
        result = audit(self.docs)
        self.assertFalse(result['checks']['every_operational_csv_cell'])
        self.assertFalse(result['publication_coherent'])

    def test_price_day_or_identity_drift_fails(self):
        self.mutate(FILES[0], lambda d: d['stocks'][0].update(price_date='2020-01-01'))
        result = audit(self.docs)
        self.assertFalse(result['checks']['priced_issuer_identity'])
        self.assertFalse(result['publication_coherent'])

    def test_missing_price_without_a_reason_fails(self):
        def remove_price_and_reasons(data):
            code = data['stocks'].pop(0)['code']
            for field in ('collection_errors', 'history_exclusions'):
                data[field] = [row for row in data[field] if row[0] != code]
            data['error_count'] = len(data['collection_errors'])

        self.mutate(FILES[0], remove_price_and_reasons)
        result = audit(self.docs)
        self.assertEqual(result['unexplained_unpriced_count'], 1)
        self.assertFalse(result['checks']['all_unpriced_issuers_explained'])
        self.assertFalse(result['publication_coherent'])

    def test_zero_volume_display_loss_fails(self):
        def remove(d):
            code = next(code for code, r in d['rows'].items() if r['state'] == 'zero_volume')
            del d['rows'][code]
        self.mutate(FILES[5], remove)
        result = audit(self.docs)
        self.assertFalse(result['checks']['zero_volume_display_retained'])
        self.assertFalse(result['publication_coherent'])

    def test_regular_close_attachment_tracks_snapshot_and_rejects_identity_drift(self):
        raw = (self.docs / FILES[0]).read_bytes()
        market = json.loads(raw)
        stock = next(row for row in market['stocks']
                     if isinstance(row.get('price'), (int, float)) and row['price'] > 0)
        day = market['date']
        quote = {'name': stock['name'], 'market': stock['market'],
                 'analysis_close': stock['price'], 'close': stock['price']}
        report = {
            'schema': 'v3-current-corroborated-regular-closes-1',
            'source': 'DATED_PUBLIC_FDR_CLOSE_CORROBORATED_WITH_KRX',
            'asof': day, 'source_market_sha256': sha256(raw).hexdigest(),
            'public_cache_sha256': 'b' * 64,
            'official_source_sha256': {'KOSPI': 'c' * 64, 'KOSDAQ': 'd' * 64},
            'historical_ohlcv_regular_session_verified': False, 'score_recalculated': False,
            'requested_count': len(market['stocks']), 'matched_count': 1,
            'states': {'matched': 1, 'public_close_unavailable': len(market['stocks']) - 1},
            'rows': {stock['code']: quote},
        }
        path = self.docs / 'v3-regular-close-prices.json'
        path.write_text(json.dumps({**report, 'asof': '2020-01-01'}), encoding='utf-8')
        self.assertEqual(audit(self.docs)['pages']['regular_close']['status'],
                         'awaiting_current_market_day')
        path.write_text(json.dumps({**report, 'source_market_sha256': 'a' * 64}), encoding='utf-8')
        self.assertEqual(audit(self.docs)['pages']['regular_close']['status'],
                         'awaiting_current_market_snapshot')
        path.write_text(json.dumps(report), encoding='utf-8')
        result = audit(self.docs)
        self.assertTrue(result['publication_coherent'])
        self.assertEqual(result['pages']['regular_close']['linked'], 1)
        quote['name'] = 'Different issuer'
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertFalse(audit(self.docs)['publication_coherent'])

    def test_score_rank_drift_is_rejected_by_python_and_pages(self):
        def change(d):
            row = next(r for r in d['rows'].values() if r['rank'] == 1)
            row['rank'] = 2
        self.mutate(FILES[2], change)
        result = audit(self.docs)
        self.assertFalse(result['checks']['score_parts_totals_ranks_and_roster'])
        self.assertFalse(result['checks']['pages_attachment_and_details'])
        self.assertFalse(result['publication_coherent'])


if __name__ == '__main__':
    unittest.main()
