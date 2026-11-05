import copy
from datetime import date,timedelta
import gzip
from hashlib import sha256
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import v3_actual_money_participation as m
from test_v3_krx_money_horizon_research import input_cohorts
from test_v3_krx_money_forward import FakeStore,weekdays
from test_v3_krx_calendar_week_money import Client,days as week_days
from v3_krx_money_backfill import _cohort_at_recent_first, _path, SCHEMA as HIST_SCHEMA
from v3_krx_money_forward import _prefix,SCHEMA as FORWARD_SCHEMA
from v3_krx_calendar_week_money import collect as weeks_collect
from test_v3_volume_participation import stock,DAY
from v3_volume_participation import build, observed
from test_v3_dart_rolling_book_reference import fixture as market_fixture


def fixture():
    store=FakeStore()
    # 96 full historical cohorts plus the fixed two-session residue.
    stop=date(2026,10,16); dates=[]
    while len(dates)<482:
        if stop.weekday()<5: dates.append(stop.isoformat())
        stop-=timedelta(days=1)
    dates.reverse()
    forward=weekdays('2026-10-15',15)
    template=input_cohorts(1)[0]['cohort']
    template.update(source_api_calls=10,raw_vendor_source_retained=False,
                    complete_five_session_issuer_market_pairs=1,
                    market_date_coverage={str(i):{} for i in range(10)})
    windows=[_cohort_at_recent_first(dates,i) for i in range(96)]
    windows += [forward[5*i:5*(i+1)] for i in range(3)]
    for window in windows:
        data=copy.deepcopy(template)
        data.update(window_start=window[0],window_end=window[-1])
        store.files[_path(window)]=gzip.compress(json.dumps(data).encode())
    store.files[m.CHECKPOINT]=json.dumps(dict(schema=HIST_SCHEMA,start='2016-10-17',end='2026-10-16',
        calendar_candidate_hash='a'*64,next_window_index=96,full_issuer_bucket_records=96,
        compressed_factor_bytes=10000,raw_source_file_count=0,
        last_window_file_sha256=sha256(store.files[_path(windows[95])]).hexdigest())).encode()
    store.files[m.FORWARD]=json.dumps(dict(schema=FORWARD_SCHEMA,historical_anchor_end='2026-10-16',
        next_window_index=3,completed_candidate_prefix_sha256=_prefix(forward,3),
        issuer_cohort_records=3,compressed_factor_bytes=1000,raw_source_file_count=0,
        last_window_file_sha256=sha256(store.files[_path(forward[10:15])]).hexdigest())).encode()
    with patch('builtins.print'):
        weeks_collect(store,Client(),asof='2026-11-04',max_weeks=240,day_loader=week_days)
    catalog={'date':'2026-11-04','listed_total':1,'rows':[{'code':'000001','market':'KOSPI','name':'fixture'}]}
    return store,dates,forward,catalog


def report_fixture(catalog):
    return dict(schema=m.SCHEMA,model=m.MODEL,asof=catalog['date'],points_budget=6,
        source_catalog_sha256=m._stable_digest(catalog),source_private_revision='a'*40,
        source_checkpoint_sha256={str(i):'a'*64 for i in range(3)},
        daily_windows=list(m.DAILY),calendar_week_windows=list(m.WEEKLY_WINDOWS),
        daily_cohorts_inspected=96,calendar_weeks_inspected=240,
        listed_total=catalog['listed_total'],eligible_issuers=0,records={},
        calendar_week_end=m.closed_week_end(catalog['date']),weekly_session_end=(date.fromisoformat(m.closed_week_end(catalog['date']))-timedelta(days=2)).isoformat(),
        provisional_personal_use_only=True,original_amounts_or_bars_published=False,
        independent_regular_session_verified=False,corporate_actions_and_pit_verified=False,
        investment_score_certified=False,single_day_money_component_points=0,
        missing_or_zero_baseline_is_unavailable=True)


class ActualMoneyTests(unittest.TestCase):
    def test_missing_period_and_zero_baseline_reason_are_not_issuer_or_price_leaks(self):
        daily=[2]*96
        self.assertEqual(m.family_coverage_reason(daily,(4,12,24,48)),'complete')
        self.assertEqual(m.family_coverage_reason(daily[:95]+[None],(4,12,24,48)),
                         'incomplete_periods')
        self.assertEqual(m.family_coverage_reason([2]*4+[0]*4+[2]*88,(4,12,24,48)),
                         'zero_baseline')
        with self.assertRaises(m.ActualMoneyError):
            m.family_coverage_reason([2]*95+[-1],(4,12,24,48))

    def test_coverage_gap_aggregate_reconciles_all_listed_without_fake_scores(self):
        store,days,forward,catalog=fixture()
        catalog['listed_total']=3
        catalog['rows']+= [
            {'code':'000002','market':'KOSPI','name':'insufficient A'},
            {'code':'000003','market':'KOSDAQ','name':'insufficient B'}]
        report=m.collect(store,asof='2026-11-04',catalog=catalog,source_revision='a'*40,
                         days=days,digest='a'*64,forward_days=forward,week_loader=week_days)
        d=report['coverage_gap_diagnostic']
        self.assertEqual(report['eligible_issuers'],1)
        self.assertEqual(d['full_fresh_evidence_issuers'],1)
        self.assertEqual(d['fresh_unscored_issuers'],2)
        self.assertEqual(d['reason_pair_counts'],{'incomplete_periods__incomplete_periods':2})
        comparison=report['comparison_window_coverage']
        self.assertEqual(comparison['daily_comparison_eligible_issuers'],
                         {str(n):1 for n in m.DAILY})
        self.assertEqual(comparison['weekly_comparison_eligible_issuers'],
                         {str(n):1 for n in m.WEEKLY_WINDOWS})
        self.assertFalse(comparison['issuer_window_flags_or_amounts_published'])
        summary=m.coverage_summary(report)
        self.assertEqual(summary['schema'],m.COVERAGE_SUMMARY_SCHEMA)
        self.assertEqual(summary['eligible_issuers'],1)
        self.assertEqual(summary['coverage_gap_diagnostic'],d)
        self.assertEqual(summary['comparison_window_coverage'],comparison)
        self.assertNotIn('records',summary)
        self.assertNotIn('000001',json.dumps(summary))
        self.assertNotIn('000002',json.dumps(summary))
        self.assertLess(len(json.dumps(summary)),2400)
        self.assertNotIn('000002',report['records'])
        self.assertNotIn('000003',report['records'])
        self.assertNotIn('amount_100k',json.dumps(d))
        self.assertEqual(len(m.usable_records(report,catalog)),1)
        for change in ({'fresh_unscored_issuers':3},
                       {'reason_pair_counts':{'complete__complete':2}},
                       {'issuer_amounts_or_coverage_timeseries_published':True}):
            corrupted=copy.deepcopy(report)
            corrupted['coverage_gap_diagnostic'].update(change)
            self.assertEqual(m.usable_records(corrupted,catalog),{})
        bad_counts=copy.deepcopy(report)
        bad_counts['comparison_window_coverage']['weekly_comparison_eligible_issuers']['20']=0
        self.assertEqual(m.usable_records(bad_counts,catalog),{})

    def test_shorter_windows_can_be_observable_without_full_six_point_eligibility(self):
        weeks=[3]*10+[None]*230
        self.assertEqual(m.family_coverage_reason(weeks,(5,)),'complete')
        self.assertEqual(m.family_coverage_reason(weeks,(10,)),'incomplete_periods')
        self.assertEqual(m.family_coverage_reason(weeks,(120,)),'incomplete_periods')
        self.assertIsNone(m.family(weeks,m.WEEKLY_WINDOWS))

    def test_ratio_scale_invariance_persistence_and_concentration(self):
        self.assertEqual(m.window_strength([3]*5,[1]*5),1)
        self.assertEqual(m.window_strength([300]*5,[100]*5),1)
        self.assertLess(m.window_strength([11,1,1,1,1],[1]*5),1)
        self.assertEqual(m.window_strength([15,0,0,0,0],[1]*5),0)
        self.assertEqual(m.window_strength([1]*5,[1]*5),0)
        self.assertIsNone(m.window_strength([1]*5,[0]*5))

    def test_missing_history_is_not_a_zero_score(self):
        self.assertIsNone(m.provisional_points([1]*95,[1]*240))
        self.assertIsNone(m.provisional_points([1]*96,[1]*239))
        self.assertIsNone(m.provisional_points([1]*95+[None],[1]*240))
        self.assertEqual(m.provisional_points([1]*96,[1]*240),0)

    def test_private_full_chain_projects_only_public_score_and_dates(self):
        store,days,forward,catalog=fixture()
        result=m.collect(store,asof='2026-11-04',catalog=catalog,source_revision='a'*40,
                         days=days,digest='a'*64,forward_days=forward,week_loader=week_days)
        self.assertEqual(result['eligible_issuers'],1)
        self.assertEqual(result['records']['000001']['points'],0)
        self.assertIn('r1c_structure',result['records']['000001'])
        self.assertTrue(m.usable_records(result,catalog))
        self.assertEqual(len(m.usable_records(result,catalog)),1)
        text=json.dumps(result)
        for field in ('amount_100k','ACC_TRDVAL','TDD_CLSPRC','"ratio"','total_actual_amount'):
            self.assertNotIn(field,text)
        self.assertFalse(result['investment_score_certified'])
        tampered=copy.deepcopy(result)
        tampered['records']['000001']['r1c_structure']['growth6']=10
        self.assertEqual(m.usable_records(tampered,catalog),{})
        # Removing a committed middle file must fail, even with the same tail.
        del store.files[_path(_cohort_at_recent_first(days,10))]
        with self.assertRaises(ValueError):
            m.collect(store,asof='2026-11-04',catalog=catalog,source_revision='a'*40,
                      days=days,digest='a'*64,forward_days=forward,week_loader=week_days)

    def test_complete_periods_keep_independent_dates_without_waiting_for_market_day_alignment(self):
        store,days,forward,catalog=fixture()
        catalog['date']='2026-11-05'
        result=m.collect(store,asof=catalog['date'],catalog=catalog,source_revision='a'*40,
                         days=days,digest='a'*64,forward_days=forward,week_loader=week_days)
        record=result['records']['000001']
        self.assertEqual(record['daily_asof'],'2026-11-04')
        self.assertEqual(record['weekly_session_end'],'2026-10-30')
        self.assertEqual(record['asof'],'2026-10-30')
        self.assertEqual(record['computed_asof'],'2026-11-05')
        self.assertEqual(len(m.usable_records(result,catalog)),1)
        # A later completed calendar week can follow an older daily endpoint.
        record.update(daily_asof='2026-10-28',weekly_session_end='2026-10-30',asof='2026-10-28')
        self.assertEqual(len(m.usable_records(result,catalog)),1)
        bad=copy.deepcopy(result);bad['records']['000001']['computed_asof']='2026-10-29'
        self.assertFalse(m.usable_records(bad,catalog))

    def test_uncommitted_latest_week_and_future_daily_endpoint_are_blocked(self):
        store,days,forward,catalog=fixture()
        state=json.loads(store.files[m.WEEKLY]);state['history_completed']=239
        store.files[m.WEEKLY]=json.dumps(state).encode()
        with self.assertRaises(ValueError):
            m.collect(store,asof='2026-11-04',catalog=catalog,source_revision='a'*40,
                      days=days,digest='a'*64,forward_days=forward,week_loader=week_days)
        with self.assertRaises(m.ActualMoneyError):
            m.collect(store,asof='2026-11-03',catalog=dict(catalog,date='2026-11-03'),
                      source_revision='a'*40,days=days,digest='a'*64,forward_days=forward,week_loader=week_days)

    def test_name_market_date_and_certification_validation(self):
        _,catalog=market_fixture();report=report_fixture(catalog)
        item=catalog['rows'][0]
        rec={'market':item['market'],'asof':catalog['date'],'points':2.125,'model':m.MODEL,
             'weekly_session_end':report['weekly_session_end'],'issuer_name':item['name']}
        report.update(records={item['code']:rec},eligible_issuers=1)
        self.assertEqual(len(m.usable_records(report,catalog)),1)
        for field,value in [('points',float('nan')),('points',True),('points',7),
                            ('asof','2027-01-01'),('market','OTHER'),('issuer_name','reused issuer')]:
            bad=copy.deepcopy(report);bad['records'][item['code']][field]=value
            self.assertFalse(m.usable_records(bad,catalog))
        bad=copy.deepcopy(report);bad['investment_score_certified']=True
        self.assertFalse(m.usable_records(bad,catalog))
        # New listing/census and later day keep matched prior company evidence.
        later=copy.deepcopy(catalog);later['date']='2026-10-20';later['listed_total']+=1
        later['rows'].append({'code':'999999','market':'KOSDAQ','name':'new'})
        self.assertEqual(len(m.usable_records(report,later)),1)

    def test_only_six_point_factor_changes_and_missing_receipt_keeps_baseline(self):
        market,catalog=market_fixture()
        for item in catalog['rows']:
            for row in market['stocks']:
                if item['code']==row['code']:item['name']=row['name']
        report=report_fixture(catalog)
        target=market['stocks'][0]
        original_market=target["market"]
        target.update(stock())
        target["market"]=original_market
        report.update(records={target['code']:{'market':target['market'],'asof':catalog['date'],
            'points':1.2345,'model':m.MODEL,'weekly_session_end':report['weekly_session_end'],
            'issuer_name':target['name']}},eligible_issuers=1)
        report['records'][target['code']]['asof']='2026-10-18'
        before=build(market,catalog);after=build(market,catalog,money_source=report)
        self.assertEqual(after['records'][target['code']]['subpoints']['sustained_volume'],1.2345)
        for code,r in before['records'].items():
            other=after['records'][code]
            for key in ('ma20_ma60_cross_near_ma240','obv_price_not_reflected','post_spike_absorption'):
                self.assertEqual(r['subpoints'][key],other['subpoints'][key])
        from v3_current_observed_scorecard import generate
        from v3_current_price_replay import build as price_build
        from v3_issuer_factor_ledger import build as ledger_build
        rows,_=generate(market,catalog,after,price_build(market,catalog),money_source=report)
        ledger=ledger_build(catalog,rows)
        self.assertEqual(ledger['rows'][target['code']]['factors']['volume']['asof'],'2026-10-18')
        bad=copy.deepcopy(report);bad['investment_score_certified']=True
        self.assertEqual(build(market,catalog,money_source=bad),before)

    def test_new_money_component_does_not_depend_on_retired_share_volume_ratio(self):
        market,catalog=market_fixture()
        for item in catalog['rows']:
            for row in market['stocks']:
                if item['code']==row['code']:item['name']=row['name']
        target=market['stocks'][0];market_name=target['market']
        target.update(stock());target['market']=market_name
        report=report_fixture(catalog)
        report.update(eligible_issuers=1,records={target['code']:dict(
            market=market_name,issuer_name=target['name'],asof=catalog['date'],
            weekly_session_end=report['weekly_session_end'],points=2.5,model=m.MODEL)})
        for ratio in (None,1000,False):
            with self.subTest(retired_share_ratio=ratio):
                target['volume_ratio']=ratio
                self.assertNotIn(target['code'],build(market,catalog)['records'])
                current=build(market,catalog,money_source=report)['records'][target['code']]
                self.assertEqual(current['subpoints']['sustained_volume'],2.5)
                self.assertEqual(current['subpoints']['obv_price_not_reflected'],4)
                self.assertEqual(current['observed_available_max'],10)
        target['divergence']=False
        self.assertNotIn(target['code'],build(market,catalog,money_source=report)['records'])

    def test_local_snapshot_path_escape_and_non_money_paths_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=m.LocalPrivateSnapshot(tmp,'synthetic-test-token')
            for path in ('../secret','krx/2026/other','derived/money-five-session/../../../../secret'):
                with self.assertRaises(m.ActualMoneyError):store.get_content(path)

    def test_expected_collection_wait_preserves_existing_report_and_emits_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            catalog={'date':'2026-11-04','rows':[]}
            (root/'v22-market-catalog.json').write_text(json.dumps(catalog))
            target=root/m.OUTPUT;target.write_text('previous valid score untouched')
            summary_path=root/m.COVERAGE_SUMMARY
            summary_path.write_text('previous valid summary untouched')
            output=root/'step-output'
            with patch.object(m,'collect',side_effect=m.ActualMoneyError('money_calendar_week_history_incomplete')), patch.dict(
                'os.environ',{'GITHUB_OUTPUT':str(output),'V3_PRIVATE_ARCHIVE_TOKEN':'synthetic-offline-no-secret'}),patch('builtins.print'):
                m.main(['--private-snapshot',directory,'--private-revision','a'*40,
                        '--docs',directory,'--allow-incomplete'])
            self.assertEqual(target.read_text(),'previous valid score untouched')
            self.assertEqual(summary_path.read_text(),'previous valid summary untouched')
            self.assertEqual(output.read_text(),'ready=false\n')
            workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-actual-money-score.yml').read_text()
            offline,live=workflow.split('  publish:',1)
            self.assertNotIn('secrets.',offline)
            self.assertIn("if: steps.projection.outputs.ready == 'true'",live)
            self.assertIn('if: always()',live)
            self.assertLess(live.index('rm -rf -- .money-private'),live.index('git add -- docs/'))
            self.assertNotIn('upload-artifact',workflow)

    def test_public_volume_consumer_imports_without_any_site_packages(self):
        import subprocess,sys
        result=subprocess.run([sys.executable,'-S','-c',
            "import sys; import v3_actual_money_contract, v3_volume_participation; "
            "assert 'requests' not in sys.modules; assert 'official_probe.clients' not in sys.modules"],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)

    def test_forward_retries_only_transient_source_errors(self):
        from v3_krx_money_forward import RetryingMoneyClient
        client=Client();client.fail_once=True
        with patch('v3_krx_money_forward.retry_clock.sleep'):
            adapter=RetryingMoneyClient(client);adapter.daily_market('KOSPI','2026-11-04')
        self.assertEqual(adapter.requests,2)
        from official_probe.clients import KRXProbeError
        with patch.object(client,'daily_market',side_effect=KRXProbeError('KOSPI','http_forbidden',403)) as call:
            with self.assertRaises(KRXProbeError):adapter.daily_market('KOSPI','2026-11-04')
            self.assertEqual(call.call_count,1)
