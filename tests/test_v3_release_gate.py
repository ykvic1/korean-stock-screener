"""v3 release gate must not inherit misleading v2 / research approvals."""
from __future__ import annotations
import copy
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from v3_release_gate import assess_v3, main, GATES

ROOT = Path(__file__).resolve().parents[1]


def make_inputs():
    codes=[str(i).zfill(6) for i in range(53)]
    session = {
        'price_session':'KRX_REGULAR_0900_1530_ASIA_SEOUL',
        'volume_session':'KRX_REGULAR_0900_1530_ASIA_SEOUL',
        'final':True,
        'official_close_status':'matched',
        'history_session_verified':True,
        'confirmed_scoring_allowed':True,
        'official_source_sha256':'a'*64,
    }
    stocks=[{'code':code,'session_validation':copy.deepcopy(session)} for code in codes]
    reliability={
        'asof':'2026-10-16','requested':53,'stocks':stocks,
        'regular_session_verified':53,'independently_matched':53,
        'baseline_cohort':{
            'requested':53,'stocks':[{'code':x} for x in codes],
            'numeric_segment_usable':53,'full_history_verified':53,
            'independently_matched_recent':53,
        }
    }
    quant={'asof':'2026-10-16','requested_symbols':53,'stocks':[{'code':x} for x in codes]}
    daily={'date':'2026-10-16','candidate_count':53,
           'flow_confirmed_count':53,'stocks':[{'code':x} for x in codes]}
    baseline={'stocks':[{'code':x} for x in codes]}
    return reliability,quant,daily,baseline


class V3ReadinessTests(unittest.TestCase):
    def test_all_internal_flags_true_do_not_bypass_independent_v3_contracts(self):
        r,q,d,b=make_inputs()
        output=assess_v3(r,q,d,b,proof={'v3_ready':True,
                                          'pit_verified':True,
                                          'regular_only_volume':True})
        self.assertTrue(output['gates']['consistent_snapshots']['passed'])
        self.assertTrue(output['gates']['fixed_53_price_history']['passed'])
        self.assertTrue(output['gates']['independently_reconciled_history']['passed'])
        self.assertFalse(output['gates']['regular_session_ohlcv']['passed'])
        for gate in ('point_in_time_universe','verified_investor_flows',
                     'pit_and_scope_safe_financials','sector_pit_membership',
                     'score_model_verified','persistent_incremental_store',
                     'v3_end_to_end_verified'):
            self.assertFalse(output['gates'][gate]['passed'],gate)
        self.assertEqual(len(output['gates']),len(GATES))
        self.assertFalse(output['v3_release_ready'])
        self.assertFalse(output['v3_data_scoring_authorized'])
        self.assertIsNone(output['v3_confirmed_score'])
        self.assertIsNone(output['v3_confirmed_rank'])

    def test_mismatched_dates_stock_counts_and_baseline_identity_fail_closed(self):
        r,q,d,b=make_inputs()
        failures={
            'date':lambda: q.update(asof='2026-10-15'),
            'count':lambda: q['stocks'].pop(),
            'baseline':lambda: b['stocks'].pop(),
            'history':lambda: r['baseline_cohort'].update(full_history_verified=52),
            'regular':lambda: r.update(regular_session_verified=52),
            'independent':lambda: r.update(independently_matched=52),
        }
        for name in failures:
            rr,qq,dd,bb=make_inputs()
            if name=='date':qq['asof']='2026-10-15'
            if name=='count':qq['stocks'].pop()
            if name=='baseline':bb['stocks'].pop()
            if name=='history':rr['baseline_cohort']['full_history_verified']=52
            if name=='regular':rr['regular_session_verified']=52
            if name=='independent':rr['independently_matched']=52
            output=assess_v3(rr,qq,dd,bb)
            with self.subTest(name=name):
                self.assertFalse(output['v3_release_ready'])
                if name in ('date','count'):
                    self.assertFalse(output['gates']['consistent_snapshots']['passed'])
                if name in ('baseline','history'):
                    self.assertFalse(output['gates']['fixed_53_price_history']['passed'])
                if name=='independent':
                    self.assertFalse(output['gates']['independently_reconciled_history']['passed'])

    def test_missing_files_or_fake_proof_cannot_promote_score(self):
        out=assess_v3(None,None,None,None,proof={
            'certified':True,'approved_by_user':True,'total_score':100})
        self.assertEqual(out['passed_count'],0)
        self.assertFalse(out['v3_release_ready'])
        self.assertFalse(out['v3_data_scoring_authorized'])
        self.assertIsNone(out['v3_confirmed_rank'])

    def test_current_published_v22_snapshot_is_blocked(self):
        def data(path):
            return json.loads((ROOT/path).read_text(encoding='utf-8'))
        reliability=data('docs/reliability.json')
        quant=data('docs/quant_v22.json')
        daily=data('docs/data.json')
        r=assess_v3(reliability,quant,daily,
                    data('config/v22_baseline_53.json'))
        # The daily public market snapshot advances every trading day, while
        # historical v2.2 reliability/quant evidence may lag behind. An
        # unmatched snapshot must FAIL CLOSED, not be assigned a hard-coded
        # prior market date as though all feeds were synchronized.
        dates=(reliability.get('asof'),quant.get('asof'),daily.get('date'))
        matched=all(isinstance(d,str) for d in dates) and len(set(dates))==1
        self.assertEqual(r['asof'],dates[0] if matched else None)
        if not matched:
            self.assertFalse(r['gates']['consistent_snapshots']['passed'])
        self.assertFalse(r['v3_release_ready'])
        self.assertEqual(r['measured']['regular_session_verified'],0)
        self.assertEqual(r['measured']['independent_matched'],0)
        self.assertEqual(r['measured']['daily_flow_confirmed'],0)
        self.assertFalse(r['gates']['fixed_53_price_history']['passed'])
        self.assertFalse(r['gates']['verified_investor_flows']['passed'])
        self.assertIsNone(r['v3_confirmed_score'])

    def test_cli_readonly_offline_output_and_docs_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            cwd=os.getcwd()
            try:
                os.chdir(folder)
                r,q,d,b=make_inputs()
                for name,value in [('rel',r),('quant',q),('daily',d),('base',b)]:
                    Path(name+'.json').write_text(json.dumps(value),encoding='utf-8')
                args=['--reliability','rel.json','--quant','quant.json',
                      '--daily','daily.json','--baseline','base.json',
                      '--assert-blocked','--output','evidence/v3_gate.json']
                with redirect_stdout(StringIO()):
                    out=main(args)
                self.assertEqual(out['v3_release_ready'],False)
                saved=json.loads(Path('evidence/v3_gate.json').read_text())
                self.assertIsNone(saved['v3_confirmed_rank'])
                with self.assertRaises(SystemExit),redirect_stdout(StringIO()):
                    main(args[:-1]+['docs/v3_public.json'])
            finally:
                os.chdir(cwd)


if __name__=='__main__':
    unittest.main()
