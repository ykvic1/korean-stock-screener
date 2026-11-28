import json
from pathlib import Path
import unittest
from unittest.mock import patch

from v3_dart_2025_owner_profit_probe import ProfitProbeError, _validated_record
from v3_kind_owner_profit_audit import (
    CODE, RECEIPT, REVIEWED_ANCHORS, REVIEWED_FILINGS, REVIEWED_PROFIT, admit_reviewed, audit, saved_anchors,
)


class KindOwnerProfitAuditTest(unittest.TestCase):
    def test_receipt_anchor_checks_use_the_current_annual_column(self):
        # Minimal rows transcribed from the public KIND annual filing.
        body = '''
        <p>디와이파워 2025.01.01 2025.12.31</p>
        <p>연결 포괄손익계산서</p>
        <table>
          <tr><td>Ⅰ.수익(매출액)</td><td>382,750,873,929</td></tr>
          <tr><td>Ⅴ.영업이익(손실)</td><td>30,429,405,841</td></tr>
          <tr><td>XⅢ.당기순이익(손실)</td><td>25,143,819,961</td></tr>
        </table>
        <p>연결 현금흐름표</p>
        <table><tr><td>Ⅰ.영업활동현금흐름</td><td>23,102,288,824</td></tr></table>
        <p>34. 주당이익 (연결)</p>
        <table>
          <tr><td>지배주주 당기순이익</td><td>25,143,819,961</td></tr>
          <tr><td>가중평균유통보통주식수</td><td>11,035,851</td></tr>
          <tr><td>기본주당이익</td><td>2,278</td></tr>
        </table>
        '''.encode('utf-8')
        anchors = {'revenue': 382750873929, 'operating_income': 30429405841,
                   'cfo': 23102288824}
        self.assertEqual(audit(body, anchors, 2278), 25143819961)
        for field, value in anchors.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                audit(body, {**anchors, field: value + 1}, 2278)
        with self.assertRaises(ValueError):
            audit(body, anchors, 1987)

    def test_reviewed_fact_admission_requires_saved_anchors_and_current_receipt(self):
        day = '2026-11-27'
        base = {'code': CODE, 'name': '디와이파워', 'market': 'KOSPI',
                'annual_receipt_id': RECEIPT, 'fiscal_year': 2025, 'fs_div': 'CFS'}
        missing = {'status': 'missing_standard_account'}
        retained = {CODE: missing}
        expected = {CODE: base}
        with patch('v3_kind_owner_profit_audit.saved_anchors',
                   return_value={**REVIEWED_ANCHORS, 'reported_eps': 2277}):
            admit_reviewed(Path('docs'), day, expected, retained)
        self.assertEqual(retained[CODE], missing)
        with patch('v3_kind_owner_profit_audit.saved_anchors', return_value=REVIEWED_ANCHORS):
            admit_reviewed(Path('docs'), day,
                           {CODE: {**base, 'annual_receipt_id': '20260317001465'}}, retained)
            self.assertEqual(retained[CODE], missing)
            admit_reviewed(Path('docs'), day, expected, retained)
            self.assertEqual(retained[CODE]['owners_profit_krw'], REVIEWED_PROFIT)
            _validated_record(retained[CODE], day)
            admitted = dict(retained[CODE])
            admit_reviewed(Path('docs'), day, expected, retained)
            self.assertEqual(retained[CODE], admitted)
        for invalid_value in (REVIEWED_PROFIT + 1, float(REVIEWED_PROFIT)):
            with self.subTest(invalid_value=invalid_value), self.assertRaises(ProfitProbeError):
                _validated_record({**retained[CODE], 'owners_profit_krw': invalid_value}, day)

    def test_additional_issuer_uses_its_own_filing_and_skips_existing_observation(self):
        code = '065370'
        day = '2026-11-27'
        filing = REVIEWED_FILINGS[code]
        base = {'code': code, 'name': '위세아이텍', 'market': 'KOSDAQ',
                'annual_receipt_id': filing['receipt'], 'fiscal_year': 2025, 'fs_div': 'CFS'}
        expected = {code: base}
        retained = {code: {'status': 'missing_standard_account'}}
        with patch('v3_kind_owner_profit_audit.saved_anchors',
                   return_value=filing['anchors']) as anchors:
            self.assertEqual(admit_reviewed(Path('docs'), day, expected, retained), 1)
            anchors.assert_called_once_with(Path('docs'), code, filing['receipt'])
            self.assertEqual(admit_reviewed(Path('docs'), day, expected, retained), 0)
            self.assertEqual(anchors.call_count, 1)
        _validated_record(retained[code], day)
        self.assertEqual(retained[code]['owners_profit_krw'], 1_454_510_181)
        with self.assertRaises(ProfitProbeError):
            _validated_record({**retained[code], 'kind_source_url': REVIEWED_FILINGS[CODE]['url']}, day)

    def test_current_saved_annual_receipt_and_accounts_reconcile_without_pilot_checkpoint(self):
        docs = Path(__file__).resolve().parents[1] / 'docs'
        filing = REVIEWED_FILINGS['065370']
        book = json.loads((docs / 'v3-dart-book-reference.json').read_text(encoding='utf-8'))
        if book['asof'] != '2026-11-27':
            self.skipTest('only the original review snapshot is pinned to these exact accounts')
        self.assertEqual(saved_anchors(docs, '065370', filing['receipt']), filing['anchors'])
        priority = json.loads((docs / 'v3-near-100-evidence-priority.json').read_text(encoding='utf-8'))
        if priority['asof'] != book['asof']:
            self.skipTest('saved priority is waiting for the current annual book')
