"""Fail-closed tests for exact 2025 cohort mapping without slow global corpCode ZIP."""
import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from official_probe.__main__ import run
from official_probe.pinned_2025_pilot import (
    fixed_pilot_corp_mapping, PINNED_CASES, PINNED_ASOF, SOURCE_RUNS,
)

CODES = ','.join(PINNED_CASES)
CASES = ','.join(
    f'{code}:{receipt}:{scope}' for code, (_, receipt, scope) in PINNED_CASES.items()
)


class ReviewedMappingTests(unittest.TestCase):
    def test_exact_reviewed_identity_and_receipts_are_reused(self):
        inputs = {k: (r, basis) for k, (_, r, basis) in PINNED_CASES.items()}
        mapping = fixed_pilot_corp_mapping(PINNED_ASOF, inputs)
        self.assertEqual({k: v[0] for k,v in PINNED_CASES.items()}, mapping)
        self.assertEqual(SOURCE_RUNS, (39010207507, 39010887836))
        self.assertEqual(len(mapping), 5)
        for code, corp in mapping.items():
            self.assertEqual(len(code), 6)
            self.assertEqual(len(corp), 8)

    def test_modification_and_other_year_are_rejected(self):
        inputs = {k: (r, basis) for k, (_, r, basis) in PINNED_CASES.items()}
        tests = [
            ('2026-10-17', inputs),
            ('2026-10-15', inputs),
            (PINNED_ASOF, dict(list(inputs.items())[:-1])),
            (PINNED_ASOF, {**inputs, '280360': ('20260312000908', 'OFS')}),
            (PINNED_ASOF, {**inputs, '280360': ('20260312000907', 'CFS')}),
        ]
        for day, cases in tests:
            with self.subTest(day=day, cases=cases), self.assertRaises(ValueError):
                fixed_pilot_corp_mapping(day,cases)

    def test_pinned_mode_never_downloads_entire_corp_code_and_blocks_scoring(self):
        mapping = {k: c for k,(c,_,_) in PINNED_CASES.items()}
        class FakeClient:
            def __init__(self, key, session=None):
                self.research_request_timings = []
                self.timeout = None
            def set_research_timeout(self, seconds):
                self.timeout = seconds
            def corp_mapping(self):
                raise AssertionError('Global corpCode ZIP download must not occur')
        def check(client, corp, year, asof, receipt, fs_div='OFS'):
            self.assertIn(corp, mapping.values())
            self.assertEqual(asof, '2026-10-16')
            self.assertEqual(year, 2025)
            self.assertEqual(fs_div, 'OFS')
            return {'status': 'partial_or_different_values_unverified',
                    'metrics': None, 'confirmed_scoring_allowed':False,
                    'investment_data_ready':False}
        with tempfile.TemporaryDirectory() as root:
            src = Path(root)/'data.json'
            dest = Path(root)/'audit.json'
            src.write_text(json.dumps({'date':'2026-10-16',
                'stocks':[{'code':code,'market':'KOSDAQ'} for code in mapping]}),
                encoding='utf-8')
            with patch.dict(os.environ, {'DART_API_KEY':'OFFLINE_KEY'}):
                with patch('official_probe.__main__.DARTClient', FakeClient):
                    with patch('official_probe.__main__.compare_annual_xbrl', side_effect=check):
                        with redirect_stdout(StringIO()):
                            out = run(['--asof','2026-10-16',
                                '--provider','dart','--live','--symbols',CODES,
                                '--xbrl-batch-cases',CASES,
                                '--xbrl-use-reviewed-2025-corp-map',
                                '--source',str(src),'--output',str(dest)])
            self.assertEqual(len(out['dart']['companies_sampled']), 5)
            self.assertEqual(out['dart']['xbrl_batch_pilot']['corp_mapping_request_timings'],[])
            self.assertEqual(out['dart']['xbrl_batch_pilot']['company_mapping_provenance'],
                             'two_prior_official_probe_mapping_observations_not_live')
            self.assertFalse(out['dart']['xbrl_batch_pilot']['company_mapping_live_verified_this_run'])
            self.assertFalse(out['investment_data_ready'])
            self.assertFalse(out['confirmed_scoring_allowed'])
            self.assertTrue(dest.is_file())

    def test_invalid_pinned_use_fails_before_network(self):
        with tempfile.TemporaryDirectory() as root:
            src=Path(root)/'data.json'
            src.write_text(json.dumps({'date':'2026-10-16',
                'stocks':[{'code':c,'market':'KOSDAQ'} for c in PINNED_CASES]}),
                encoding='utf-8')
            for argv in (
                ['--asof','2026-10-16','--provider','dart','--live',
                 '--xbrl-use-reviewed-2025-corp-map','--symbols',CODES],
                ['--asof','2026-10-16','--provider','dart','--live',
                 '--symbols',CODES, '--xbrl-batch-cases',
                 CASES.replace('20260312000907','20260312000908'),
                 '--xbrl-use-reviewed-2025-corp-map'],
                ['--asof','2026-10-16','--provider','both','--live',
                 '--symbols',CODES,'--xbrl-batch-cases',CASES,
                 '--xbrl-use-reviewed-2025-corp-map'],
            ):
                with self.subTest(argv=argv),redirect_stderr(StringIO()):
                    with patch('official_probe.__main__.DARTClient',
                               side_effect=AssertionError('unexpected network')),self.assertRaises(SystemExit):
                        run(argv+['--source',str(src)])


if __name__=='__main__':
    unittest.main()
