import copy
import unittest

import v3_dart_eps_prior_receipt_probe as probe
from test_v3_dart_xbrl_eps_recovery import xml
from test_v3_dart_xbrl_capex_pilot import archive


CODE = '039030'
ROW = {
    'annual_receipt_id': '20260323001573', 'fs_div': 'CFS',
    'reported_basic_eps_2023_2025': [None, 3540., 4689.],
}
ANCHOR = {
    'year': 2024, 'scope': 'CFS', 'major_annual_receipt_matched': True,
    'annual_receipt_id': '20250320001508', 'operating_cashflow_krw': 100,
}


def original_2024(**kw):
    # Same issuer/CFS contexts, but CFO anchoring explicitly belongs to 2024.
    raw = xml(values=('100', '3540', '4689'), **kw)
    return raw.replace(b'contextRef="C2025" unitRef="U"', b'contextRef="C2024" unitRef="U"')


class PriorReceiptResearchTests(unittest.TestCase):
    def test_prior_original_2023_with_exact_2024_overlap_is_research_only(self):
        values, reason = probe.extract_archive(
            archive(original_2024()), scope='CFS',
            identifiers={'00123456'}, cfo_anchor=100)
        self.assertEqual(reason, 'candidate_2023_verified')
        self.assertEqual(values, {'2023': 100., '2024': 3540.})
        record = probe.make_record(CODE, ROW, ANCHOR, values=values,
            reason=reason, archive_sha256='a' * 64,
            profile_sha256='b' * 64, asof='2026-11-06')
        probe.verify_record(record)
        self.assertEqual(record['candidate_eps_2023'], 100.)
        self.assertTrue(record['scored_eps_unchanged'])
        self.assertEqual(ROW['reported_basic_eps_2023_2025'][0], None)

    def test_overlap_conflict_cannot_inflate_score(self):
        record = probe.make_record(CODE, ROW, ANCHOR,
            values={'2023': 100., '2024': 3541.}, reason='candidate_2023_verified',
            archive_sha256='a' * 64, profile_sha256='b' * 64,
            asof='2026-11-06')
        self.assertEqual(record['reason'], '2024_overlap_conflict')
        self.assertIsNone(record['candidate_eps_2023'])
        invalid = copy.deepcopy(record)
        invalid['candidate_eps_2023'] = 100.
        with self.assertRaises(probe.PriorEPSError):
            probe.verify_record(invalid)

    def test_missing_2023_and_2024_are_not_zero(self):
        for values, expected in (({'2024': 3540.}, 'missing_2023_basic_eps'),
                                 ({'2023': 100.}, 'missing_2024_overlap')):
            record = probe.make_record(CODE, ROW, ANCHOR, values=values,
                reason='candidate_2023_verified', archive_sha256='a' * 64,
                profile_sha256='b' * 64, asof='2026-11-06')
            self.assertEqual(record['reason'], expected)
            self.assertIsNone(record['candidate_eps_2023'])

    def test_wrong_identity_scope_and_unanchored_cfo_rejected(self):
        for kwargs in ({'member': 'SeparateMember'}, {'identity': '00000000'},
                       {'concept': 'DilutedEarningsLossPerShare'},
                       {'currency': 'iso:USD'}):
            values, reason = probe.extract_archive(archive(original_2024(**kwargs)),
                scope='CFS', identifiers={'00123456'}, cfo_anchor=100)
            self.assertEqual(values, {})
            self.assertEqual(reason, 'no_verified_cfo_scope' if kwargs.get('currency') == 'iso:USD'
                             or kwargs.get('member') or kwargs.get('identity') else 'candidate_2023_verified')
        with self.assertRaises(probe.PriorEPSError):
            probe.extract_archive(archive(original_2024(cfo='101')),
                scope='CFS', identifiers={'00123456'}, cfo_anchor=100)

    def test_candidate_scope_and_receipt_must_match(self):
        reported = {'records': {CODE: ROW}}
        cash = {'records': {CODE: {'years': {'2024': ANCHOR}}}}
        self.assertEqual(len(probe.candidates(reported, cash)), 1)
        off_scope = copy.deepcopy(cash)
        off_scope['records'][CODE]['years']['2024']['scope'] = 'OFS'
        self.assertEqual(probe.candidates(reported, off_scope), [])
        prior = probe.make_record(CODE, ROW, ANCHOR,
            values={}, reason='missing_2023_basic_eps',
            archive_sha256='a' * 64, profile_sha256='b' * 64,
            asof='2026-11-06')
        self.assertEqual(probe.candidates(reported, cash, {CODE: prior}), [])
        changed = copy.deepcopy(cash)
        changed['records'][CODE]['years']['2024']['annual_receipt_id'] = '20250320009999'
        self.assertEqual(len(probe.candidates(reported, changed, {CODE: prior})), 1)


if __name__ == '__main__':
    unittest.main()
