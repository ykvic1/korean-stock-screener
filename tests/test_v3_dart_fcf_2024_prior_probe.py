import copy
import re
import unittest
import threading
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
import json
from types import SimpleNamespace

import v3_dart_cashflow_enrichment as cf
import v3_dart_fcf_2024_prior_probe as probe
import v3_dart_fcf_comparative_promotion as promo
from test_v3_dart_fcf_comparative_promotion import inputs, CODE
from test_v3_dart_cashflow_enrichment import live_inputs
from test_v3_dart_xbrl_cashflow_recovery import xml
from test_v3_dart_xbrl_capex_pilot import archive


def fixture(code=CODE):
    cash, _ = inputs()
    row = cash['records'].pop(CODE)
    cash['records'][code] = row
    anchor, source = row['years']['2023'], row['years']['2024']
    anchor.update(intangible_capex_krw=None, total_capex_krw=None, free_cashflow_krw=None)
    source.update(operating_cashflow_krw=120, intangible_capex_krw=5,
                  total_capex_krw=25, free_cashflow_krw=95)
    return cash, anchor, source


def raw_archive(**changes):
    source = xml(cfo=changes.get('source_cfo', '120'), intang=changes.get('source_intangible', '5'),
                 identity=changes.get('source_identity', '00123456'))
    prior = xml(cfo=changes.get('prior_cfo', '100'), intang=changes.get('prior_intangible', '0'),
                member=changes.get('member', 'ConsolidatedMember'),
                identity=changes.get('identity', '00123456')).replace(b'2024-', b'2023-')
    body = prior[prior.index(b'<context'):prior.rindex(b'</xbrl>')]
    body = re.sub(rb'<unit.*?</unit>', b'', body)
    body = body.replace(b'id="C"', b'id="P"').replace(b'contextRef="C"', b'contextRef="P"')
    if changes.get('missing_intangible'):
        body = re.sub(rb'<i:PurchaseOfIntangibleAssets.*?</i:PurchaseOfIntangibleAssets>', b'', body)
    if changes.get('missing_source'):
        source = source.replace(b'<i:CashFlowsFromUsedInOperatingActivities contextRef="C" unitRef="U">120</i:CashFlowsFromUsedInOperatingActivities>', b'')
    return archive(source.replace(b'</xbrl>', body + b'</xbrl>'))


def record(cash, code=CODE, **changes):
    years = cash['records'][code]['years']
    raw = raw_archive(**changes)
    proposal = probe.probe_archive(raw, years['2023'], years['2024'], {'00123456'})
    return probe.make_record(code, years['2023'], years['2024'], proposal,
                             raw, {'identity_checked': True}, '2026-11-06')


def state(row):
    return probe.snapshot('2026-11-06', 2501, {row['code']: row})


class FY2024PriorTests(unittest.TestCase):
    def test_post_close_source_lag_defers_without_provider_calls_or_checkpoint_change(self):
        output = StringIO()
        with patch('v3_dart_source_day_preflight.check', return_value={'ready': False}), \
             patch.object(probe.cf, 'retained_current_market_cashflow', side_effect=AssertionError('source replay before alignment')), \
             redirect_stdout(output):
            probe.main(['--mode', 'live', '--limit', '16', '--workers', '4', '--batches', '6', '--defer-source-lag'])
        row = json.loads(output.getvalue())
        self.assertEqual(row['provider_calls'], 0)
        self.assertIsNone(row['remaining'])
        self.assertEqual(row['status'], 'waiting_same_day_major_source')

    def parallel_fixture(self, count=12):
        cash = {'records': {}}
        mapping, by_corp, by_receipt = {}, {}, {}
        for i in range(1, count + 1):
            code = f'{i:06d}'
            row, anchor, source = fixture(code)
            anchor['annual_receipt_id'] = f'20240318{i:06d}'
            source['annual_receipt_id'] = f'20250318{i:06d}'
            cash['records'].update(row['records'])
            mapping[code] = f'{i:08d}'
            by_corp[mapping[code]] = code
            by_receipt[source['annual_receipt_id']] = code
        return cash, mapping, by_corp, by_receipt

    def test_four_live_workers_have_one_checkpoint_writer_and_no_duplicate_resume(self):
        cash, mapping, by_corp, by_receipt = self.parallel_fixture()
        barrier = threading.Barrier(4)
        lock = threading.Lock()
        started, active, peak = [], 0, 0
        writer = threading.get_ident()
        checkpoints = []
        def get(endpoint, params, stream=False):
            nonlocal active, peak
            if endpoint == 'company.json':
                code = by_corp[params['corp_code']]
                with lock:
                    started.append(code)
                    first = len(started) <= 4
                    active += 1
                    peak = max(peak, active)
                if first:
                    barrier.wait(timeout=5)
                return SimpleNamespace(json=lambda: {'status':'000', 'corp_code':mapping[code], 'stock_code':code})
            code = by_receipt[params['rcept_no']]
            raw = raw_archive(identity=mapping[code], source_identity=mapping[code])
            with lock:
                active -= 1
            return SimpleNamespace(iter_content=lambda **kwargs: [raw])
        def checkpoint(snapshot):
            self.assertEqual(threading.get_ident(), writer)
            checkpoints.append(copy.deepcopy(snapshot))
        result, saved = probe.collect(probe.targets(cash), {}, get=get, mapping=mapping,
            day='2026-11-06', total=2501, workers=4, limit=8, on_progress=checkpoint)
        self.assertEqual(peak, 4)
        self.assertEqual(result['reviewed'], 8)
        self.assertEqual(len(started), len(set(started)))
        self.assertEqual([s['reviewed_total'] for s in checkpoints], list(range(1, 9)))
        remaining = probe.targets(cash, previous=saved)
        result, saved = probe.collect(remaining, saved, get=get, mapping=mapping,
            day='2026-11-06', total=2501, workers=4, limit=8, on_progress=checkpoint)
        self.assertEqual(result['reviewed'], 4)
        self.assertEqual(len(started), 12)
        self.assertEqual(len(set(started)), 12)
        self.assertEqual(probe.targets(cash, previous=saved), [])

    def test_parallel_quota_failure_drains_verified_inflight_checkpoints_and_stops(self):
        cash, mapping, by_corp, by_receipt = self.parallel_fixture()
        barrier = threading.Barrier(4)
        failed = threading.Event()
        lock = threading.Lock()
        started, checkpoints = [], []
        def get(endpoint, params, stream=False):
            if endpoint == 'company.json':
                code = by_corp[params['corp_code']]
                with lock:
                    started.append(code)
                barrier.wait(timeout=5)
                return SimpleNamespace(json=lambda: {'status':'000','corp_code':mapping[code], 'stock_code':code})
            code = by_receipt[params['rcept_no']]
            if code == '000001':
                failed.set()
                raise probe.Prior2024Error('quota_unavailable')
            self.assertTrue(failed.wait(timeout=5))
            # Allow the failed future to surface before the other in-flight
            # sources finish. No fifth request should then be submitted.
            threading.Event().wait(.05)
            raw = raw_archive(identity=mapping[code], source_identity=mapping[code])
            return SimpleNamespace(iter_content=lambda **kwargs: [raw])
        with self.assertRaises(probe.Prior2024Error):
            probe.collect(probe.targets(cash), {}, get=get, mapping=mapping,
                day='2026-11-06', total=2501, workers=4, limit=8,
                on_progress=lambda s: checkpoints.append(copy.deepcopy(s)))
        self.assertEqual(len(started), 4)
        self.assertEqual(len(checkpoints), 3)
        self.assertEqual(set(checkpoints[-1]['records']), {'000002','000003','000004'})

    def test_quota_auth_or_unknown_status_is_not_saved_as_missing_evidence(self):
        _, anchor, source = fixture()
        for status in ('020', '010', '800', '900'):
            raw = f'<result><status>{status}</status></result>'.encode()
            with self.subTest(status=status), self.assertRaises(probe.Prior2024Error):
                probe.probe_archive(raw, anchor, source, {'00123456'})
        absent = probe.probe_archive(b'<result><status>013</status></result>',
                                     anchor, source, {'00123456'})
        self.assertEqual(absent['reason'], 'original_archive_unavailable')
        self.assertFalse(absent['candidate_parts'])

    def test_both_cfo_anchors_and_explicit_zero_can_complete_three_years(self):
        cash, anchor, source = fixture()
        original = copy.deepcopy(cash)
        row = record(cash)
        self.assertEqual(row['years']['2023']['candidate_parts'], {'intangible_capex_krw': 0})
        updated, codes = probe.apply(cash, state(row))
        self.assertEqual(codes, [CODE])
        year = updated['records'][CODE]['years']['2023']
        self.assertEqual(year['free_cashflow_krw'], 80)
        self.assertEqual(year['annual_receipt_id'], anchor['annual_receipt_id'])
        self.assertEqual(year[promo.PROOF]['source_fiscal_year'], 2024)
        self.assertEqual(year[promo.PROOF]['source_cfo_anchor'], 120)
        self.assertTrue(promo.verify_promoted_year(year, updated['records'][CODE]['years'], CODE, '2023'))
        summary = cf.summary('2026-11-06', {CODE: {}}, updated['records'], 'a' * 64)
        self.assertEqual(summary['metrics']['three_year_fcf_receipt_matched_issuers'], 1)
        self.assertFalse(summary['historical_PIT_certified'])
        self.assertEqual(cash, original)
        self.assertEqual(probe.apply(updated, state(row)), (updated, []))

    def test_scope_identity_both_cfos_known_parts_and_absence_cannot_be_guessed(self):
        for changes in ({'source_cfo': '121'}, {'prior_cfo': '101'},
                        {'source_intangible': '6'}, {'missing_source': True},
                        {'member': 'SeparateMember'}, {'identity': '00999999'},
                        {'missing_intangible': True}):
            with self.subTest(changes=changes):
                cash, _, _ = fixture()
                row = record(cash, **changes)
                self.assertFalse(row['years']['2023']['candidate_parts'])
                self.assertEqual(probe.apply(cash, state(row))[1], [])

    def test_review_markers_skip_unchanged_archives_and_reopen_on_anchor_revision(self):
        cash, _, _ = fixture()
        row = record(cash, missing_intangible=True)
        self.assertEqual(probe.targets(cash, previous={CODE: row}), [])
        changed = copy.deepcopy(cash)
        changed['records'][CODE]['years']['2024']['annual_receipt_id'] = '20250319000002'
        self.assertEqual(len(probe.targets(changed, previous={CODE: row})), 1)
        self.assertEqual(probe.apply(changed, state(row))[1], [])
        changed = copy.deepcopy(cash)
        changed['records'][CODE]['years']['2023']['operating_cashflow_krw'] += 1
        self.assertEqual(len(probe.targets(changed, previous={CODE: row})), 1)
        forged = copy.deepcopy(row)
        forged['years']['2023']['candidate_parts'] = {'intangible_capex_krw': 0}
        with self.assertRaises(probe.Prior2024Error):
            probe.apply(cash, state(forged))

    def test_only_fy2024_revision_revokes_its_2023_comparative(self):
        market, catalog, bulk = live_inputs()
        cash, _, _ = fixture('005930')
        updated, _ = probe.apply(cash, state(record(cash, '005930')))
        day, members = cf.asof_members(market, catalog, bulk)
        saved = cf.summary(day, members, updated['records'], cf.fingerprint(bulk))
        cf.verify_state(saved, market, catalog, bulk)
        for year in ('2024', '2025'):
            revised = copy.deepcopy(bulk)
            revised['records']['005930']['years'][year]['CFS']['receipt_id'] = f'{int(year)+1}0319000002'
            retained = cf.retained_current_market_cashflow(market, catalog, revised, saved)
            prior = retained['records']['005930']['years']['2023']
            if year == '2024':
                self.assertNotIn(promo.PROOF, prior)
                self.assertIsNone(prior['free_cashflow_krw'])
                self.assertEqual(prior['ppe_capex_krw'], 20)
            else:
                self.assertIn(promo.PROOF, prior)
                self.assertEqual(prior['free_cashflow_krw'], 80)

    def test_new_proof_rejects_changed_source_cfo_and_wrong_fiscal_contract(self):
        cash, _, _ = fixture()
        updated, _ = probe.apply(cash, state(record(cash)))
        for kind in ('source_cfo', 'source_year', 'amount'):
            changed = copy.deepcopy(updated['records'][CODE]['years'])
            if kind == 'source_cfo':
                changed['2024']['operating_cashflow_krw'] += 1
            elif kind == 'source_year':
                changed['2023'][promo.PROOF]['source_fiscal_year'] = 2025
            else:
                changed['2023']['intangible_capex_krw'] = 1
            with self.assertRaises(promo.ComparativePromotionError):
                promo.verify_promoted_year(changed['2023'], changed, CODE, '2023')

    def test_bounded_collection_saves_first_record_before_transport_failure(self):
        cash, _, _ = fixture()
        choices = probe.targets(cash)
        choices.append(('000001', choices[0][1], choices[0][2]))
        calls, checkpoints = [], []
        def get(endpoint, params, stream=False):
            calls.append(endpoint)
            if len(calls) == 3:
                raise probe.Prior2024Error('transport_unavailable')
            if endpoint == 'company.json':
                return SimpleNamespace(json=lambda: {'status': '000', 'corp_code': '00123456',
                                                      'stock_code': CODE})
            return SimpleNamespace(iter_content=lambda **kwargs: [raw_archive()])
        with self.assertRaises(probe.Prior2024Error):
            probe.collect(choices, {}, get=get, mapping={CODE:'00123456', '000001':'00123456'},
                day='2026-11-06', total=2501, on_progress=lambda s: checkpoints.append(copy.deepcopy(s)))
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]['reviewed_total'], 1)
        self.assertEqual(calls, ['company.json', 'fnlttXbrl.xml', 'company.json'])
        # An unmapped leading issuer cannot consume the one real-call slot
        # and starve the next independently mapped issuer forever.
        calls.clear()
        selected = [('000000', choices[0][1], choices[0][2]), choices[0]]
        result, retained = probe.collect(selected, {}, get=get, mapping={CODE:'00123456'},
            day='2026-11-06', total=2501, limit=1)
        self.assertEqual(result['reviewed'], 1)
        self.assertEqual(set(retained), {CODE})
        self.assertEqual(calls, ['company.json', 'fnlttXbrl.xml'])
        times = iter([0, 250])
        result, _ = probe.collect(choices, {}, get=lambda *a: self.fail('deadline call'),
            mapping={CODE:'00123456'}, day='2026-11-06', total=2501, clock=lambda: next(times))
        self.assertEqual(result['reviewed'], 0)


if __name__ == '__main__':
    unittest.main()
