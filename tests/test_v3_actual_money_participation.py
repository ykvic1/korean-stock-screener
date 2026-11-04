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
        calendar_week_end=m.closed_week_end(catalog['date']),weekly_session_end=catalog['date'],
        provisional_personal_use_only=True,original_amounts_or_bars_published=False,
        independent_regular_session_verified=False,corporate_actions_and_pit_verified=False,
        investment_score_certified=False,single_day_money_component_points=0,
        missing_or_zero_baseline_is_unavailable=True)


class ActualMoneyTests(unittest.TestCase):
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
        self.assertEqual(len(m.usable_records(result,catalog)),1)
        text=json.dumps(result)
        for field in ('amount_100k','ACC_TRDVAL','TDD_CLSPRC','"ratio"','total_actual_amount'):
            self.assertNotIn(field,text)
        self.assertFalse(result['investment_score_certified'])
        # Removing a committed middle file must fail, even with the same tail.
        del store.files[_path(_cohort_at_recent_first(days,10))]
        with self.assertRaises(ValueError):
            m.collect(store,asof='2026-11-04',catalog=catalog,source_revision='a'*40,
                      days=days,digest='a'*64,forward_days=forward,week_loader=week_days)

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
             'weekly_session_end':catalog['date'],'issuer_name':item['name']}
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
            'points':1.2345,'model':m.MODEL,'weekly_session_end':catalog['date'],
            'issuer_name':target['name']}},eligible_issuers=1)
        before=build(market,catalog);after=build(market,catalog,money_source=report)
        self.assertEqual(after['records'][target['code']]['subpoints']['sustained_volume'],1.2345)
        for code,r in before['records'].items():
            other=after['records'][code]
            for key in ('ma20_ma60_cross_near_ma240','obv_price_not_reflected','post_spike_absorption'):
                self.assertEqual(r['subpoints'][key],other['subpoints'][key])
        bad=copy.deepcopy(report);bad['investment_score_certified']=True
        self.assertEqual(build(market,catalog,money_source=bad),before)

    def test_local_snapshot_path_escape_and_non_money_paths_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=m.LocalPrivateSnapshot(tmp,'synthetic-test-token')
            for path in ('../secret','krx/2026/other','derived/money-five-session/../../../../secret'):
                with self.assertRaises(m.ActualMoneyError):store.get_content(path)

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
