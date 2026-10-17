"""Offline safety tests: two independent sessions, bounded concurrency and strict fail-closed."""
import threading
import time
import unittest

from official_probe.pilot_parallel import collect_pinned_pilot
from official_probe.pinned_2025_pilot import PINNED_CASES, PINNED_ASOF

CASES = {code: (receipt, scope)
         for code, (corp, receipt, scope) in PINNED_CASES.items()}


class PilotParallelTests(unittest.TestCase):
    def test_five_finished_in_order_max_two_active_no_shared_client_no_retry(self):
        lock = threading.Lock()
        active = [0]
        highest = [0]
        clients = []
        calls = []
        class Client:
            def __init__(self, key):
                self.key = key
                self.timeout = None
                self.session = None
                self.research_request_timings = []
                with lock:
                    clients.append(self)
            def set_research_timeout(self, seconds):
                self.timeout = seconds
        def compare(client, corp, year, asof, receipt, fs_div):
            with lock:
                active[0] += 1
                highest[0] = max(highest[0], active[0])
                calls.append((client,corp,year,asof,receipt,fs_div))
            try:
                time.sleep(0.03)
                client.research_request_timings.extend([
                    {'stage':'filing_list', 'result':'ok','elapsed_ms':1},
                    {'stage':'financial_json','result':'ok','elapsed_ms':1},
                    {'stage':'xbrl_zip','result':'ok','elapsed_ms':1},
                ])
                return {'status':'partial_or_different_values_unverified',
                        'investment_data_ready':False,
                        'confirmed_scoring_allowed':False}
            finally:
                with lock:
                    active[0] -= 1
        reports, meta = collect_pinned_pilot(
            key='REDACT_ME', asof=PINNED_ASOF,
            cases=CASES, client_factory=Client, compare=compare,
            workers=2, start_stagger_seconds=0)
        self.assertEqual([r['code'] for r in reports],list(PINNED_CASES))
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(clients), 5)
        self.assertEqual(len({id(c) for c in clients}),5)
        self.assertEqual(highest[0], 2)
        self.assertEqual(active[0],0)
        self.assertEqual(meta['research_workers'],2)
        self.assertEqual(meta['research_start_stagger_ms'],0)
        for report in reports:
            self.assertEqual(report['xbrl_research_timing']['network_requests_count'],3)
            self.assertFalse(report['xbrl_comparison']['investment_data_ready'])
            self.assertFalse(report['xbrl_comparison']['confirmed_scoring_allowed'])
            self.assertNotIn('REDACT_ME',str(report))
        self.assertTrue(all(c.timeout == 12 for c in clients))

    def test_one_failing_company_is_not_retried_or_silently_skipped(self):
        failing = PINNED_CASES['014680'][0]
        seen = []
        class Client:
            def __init__(self, key):
                self.research_request_timings=[]
                self.session=None
            def set_research_timeout(self, seconds):
                pass
        def compare(client, corp, year, asof, receipt, fs_div):
            seen.append(corp)
            if corp == failing:
                raise RuntimeError('SENSITIVE_URL_OR_KEY_MUST_NOT_BE_LOGGED')
            return {'status':'partial_or_different_values_unverified',
                    'investment_data_ready':False,'confirmed_scoring_allowed':False}
        results,_=collect_pinned_pilot(
            key='SECRET',asof=PINNED_ASOF,cases=CASES,
            client_factory=Client,compare=compare,
            workers=2,start_stagger_seconds=0)
        self.assertEqual(len(seen),5)
        self.assertEqual(len(set(seen)),5)
        self.assertEqual(len(results),5)
        failed = next(r for r in results if r['code']=='014680')
        self.assertEqual(failed['xbrl_comparison']['status'],'unexpected_research_error')
        self.assertIsNone(failed['xbrl_comparison']['metrics'])
        self.assertFalse(failed['xbrl_comparison']['confirmed_scoring_allowed'])
        self.assertFalse(failed['xbrl_comparison']['investment_data_ready'])
        self.assertNotIn('SENSITIVE_URL_OR_KEY_MUST_NOT_BE_LOGGED',repr(failed))

    def test_invalid_worker_count_or_modified_cohort_blocks_before_requests(self):
        def fail_factory(key):
            raise AssertionError('network attempted')
        for worker in (0,3,True,2.5):
            with self.subTest(worker=worker),self.assertRaises(ValueError):
                collect_pinned_pilot(key='x',asof=PINNED_ASOF,cases=CASES,
                    client_factory=fail_factory,compare=None,workers=worker,
                    start_stagger_seconds=0)
        for jitter in (-1,3,'1',True):
            with self.subTest(jitter=jitter),self.assertRaises(ValueError):
                collect_pinned_pilot(key='x',asof=PINNED_ASOF,cases=CASES,
                    client_factory=fail_factory,compare=None,workers=2,
                    start_stagger_seconds=jitter)
        altered=dict(CASES)
        altered['014680']=('20260318001425','OFS')
        with self.assertRaises(ValueError):
            collect_pinned_pilot(key='x',asof=PINNED_ASOF,cases=altered,
                client_factory=fail_factory,compare=None,
                workers=2,start_stagger_seconds=0)


if __name__ == '__main__':
    unittest.main()
