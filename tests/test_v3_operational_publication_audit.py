"""Catch publication drift that an internally valid score JSON cannot catch."""
import json
from pathlib import Path
import tempfile
import unittest

from v3_operational_publication_audit import audit, FILES

ROOT = Path(__file__).resolve().parents[1]


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
        self.mutate(FILES[0], lambda d: d['stocks'].pop(0))
        result = audit(self.docs)
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
