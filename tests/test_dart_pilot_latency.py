"""Offline safeguards for fail-fast and timed five-firm OpenDART research."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch

from official_probe.__main__ import run
from official_probe.clients import DARTClient, ProbeError

ASOF = '2026-10-16'
CASES = ('252990:20260316001120:OFS,'
         '280360:20260312000907:OFS')


class Response:
    status_code = 200
    def __init__(self, body):
        self.content = body


class FakeSession:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail:
            raise OSError('PRIVATE_SECRET_NEVER_PRINT')
        return Response(json.dumps({'status':'000', 'total_page':1, 'list':[]}).encode())


class DARTTimeBudgetTests(unittest.TestCase):
    def test_manual_pilot_timeout_scope_and_sanitized_timings(self):
        session = FakeSession()
        client = DARTClient('PRIVATE_SECRET_NEVER_PRINT', session=session)
        client.set_research_timeout(12)
        client._api('list.json', corp_code='01199727')
        self.assertEqual(session.calls[0][1]['timeout'], 12)
        self.assertFalse(session.calls[0][1]['allow_redirects'])
        self.assertEqual(client.research_request_timings[0]['stage'], 'filing_list')
        self.assertEqual(client.research_request_timings[0]['result'], 'ok')
        self.assertGreaterEqual(client.research_request_timings[0]['elapsed_ms'], 0)
        self.assertNotIn('PRIVATE_SECRET_NEVER_PRINT',
                         json.dumps(client.research_request_timings))
        self.assertNotIn('corp_code', json.dumps(client.research_request_timings))
        for seconds in (0, 7, 26, 12.3, '12', None, True):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                client.set_research_timeout(seconds)
        self.assertEqual(client.request_timeout, 12)

    def test_timeout_and_exception_still_leave_safe_diagnostic(self):
        session = FakeSession(fail=True)
        client = DARTClient('PRIVATE_SECRET_NEVER_PRINT', session=session)
        client.set_research_timeout(12)
        with self.assertRaises(ProbeError) as ctx:
            client._api('fnlttSinglAcntAll.json', corp_code='01199727')
        self.assertNotIn('PRIVATE_SECRET_NEVER_PRINT', str(ctx.exception))
        self.assertEqual(client.research_request_timings[-1]['stage'], 'financial_json')
        self.assertEqual(client.research_request_timings[-1]['result'], 'error')
        self.assertNotIn('PRIVATE_SECRET_NEVER_PRINT',
                         json.dumps(client.research_request_timings))

    def test_all_other_dart_modes_retain_default_25_second_timeout(self):
        session = FakeSession()
        client = DARTClient('OFFLINE_KEY', session=session)
        client._api('list.json', corp_code='01199727')
        self.assertEqual(session.calls[0][1]['timeout'], 25)

    def test_pilot_company_timing_records_network_and_local_costs(self):
        class FakeClient:
            last = None
            def __init__(self, key, session=None):
                self.timeout = None
                self.research_request_timings = []
                FakeClient.last = self
            def set_research_timeout(self, seconds):
                self.timeout = seconds
            def corp_mapping(self):
                self.research_request_timings.append(
                    {'stage':'corp_mapping_zip', 'elapsed_ms':1, 'result':'ok'})
                return {'252990':'01199727', '280360':'00123456'}
        def compare(client, corp, year, asof, receipt, fs_div='OFS'):
            for stage in ('filing_list', 'financial_json', 'xbrl_zip'):
                client.research_request_timings.append({
                    'stage':stage,'elapsed_ms':1,'result':'ok'})
            return {'status':'partial_or_different_values_unverified',
                    'metrics': None,'confirmed_scoring_allowed': False,
                    'investment_data_ready': False}
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'data.json'
            target = Path(root) / 'out.json'
            src.write_text(json.dumps({'date':ASOF,'stocks':[
                {'code':'252990', 'market':'KOSDAQ'},
                {'code':'280360', 'market':'KOSPI'}]}),encoding='utf-8')
            with patch.dict(os.environ, {'DART_API_KEY':'OFFLINE_KEY'}):
                with patch('official_probe.__main__.DARTClient', FakeClient):
                    with patch('official_probe.__main__.compare_annual_xbrl',
                               side_effect=compare):
                        with redirect_stdout(StringIO()):
                            result=run([
                                '--asof',ASOF, '--provider','dart','--live',
                                '--symbols','252990,280360',
                                '--xbrl-batch-cases',CASES,
                                '--source',str(src),'--output',str(target)])
            pilot = result['dart']['xbrl_batch_pilot']
            self.assertEqual(FakeClient.last.timeout, 12)
            self.assertEqual(pilot['request_timeout_seconds'], 12)
            self.assertEqual(pilot['soft_budget_seconds'], 210)
            self.assertEqual(pilot['completed_case_reports'], 2)
            self.assertEqual(len(result['dart']['companies_sampled']), 2)
            for co in result['dart']['companies_sampled']:
                t = co['xbrl_research_timing']
                self.assertEqual(t['network_requests_count'], 3)
                self.assertEqual([e['stage'] for e in t['sanitized_requests']],
                                 ['filing_list','financial_json','xbrl_zip'])
                self.assertGreaterEqual(t['non_network_elapsed_ms'], 0)
                self.assertFalse(result['investment_data_ready'])
                self.assertFalse(result['confirmed_scoring_allowed'])
            self.assertTrue(target.exists())

    def test_pilot_soft_budget_prevents_further_request_without_claiming_success(self):
        class FakeClient:
            def __init__(self, key, session=None):
                self.research_request_timings=[]
            def set_research_timeout(self, seconds):
                pass
            def corp_mapping(self):
                return {'252990':'01199727','280360':'00123456'}
        with tempfile.TemporaryDirectory() as root:
            src=Path(root)/'data.json'
            target=Path(root)/'out.json'
            src.write_text(json.dumps({'date':ASOF,'stocks':[
                {'code':'252990', 'market':'KOSDAQ'},
                {'code':'280360', 'market':'KOSPI'}]}), encoding='utf-8')
            clock = iter([0, 0, 0, 250, 251, 252])
            with patch.dict(os.environ, {'DART_API_KEY':'OFFLINE_KEY'}):
                with patch('official_probe.__main__.DARTClient', FakeClient):
                    with patch('official_probe.__main__.perf_counter',
                               side_effect=lambda: next(clock)):
                        with patch('official_probe.__main__.compare_annual_xbrl',
                                   side_effect=AssertionError('API should not be called')):
                            with redirect_stdout(StringIO()):
                                result=run([
                                    '--asof',ASOF,'--provider','dart','--live',
                                    '--symbols','252990,280360',
                                    '--xbrl-batch-cases',CASES,
                                    '--source',str(src),'--output',str(target)])
            pilot = result['dart']['xbrl_batch_pilot']
            self.assertEqual(pilot['completed_case_reports'], 0)
            for co in result['dart']['companies_sampled']:
                self.assertEqual(co['status'], 'pilot_soft_budget_exceeded')
                self.assertEqual(co['xbrl_comparison']['status'],
                                 'not_requested_due_to_research_time_budget')
            self.assertFalse(result['confirmed_scoring_allowed'])
            self.assertFalse(result['investment_data_ready'])


if __name__ == '__main__':
    unittest.main()
