"""Current model bridge: actual source shape and synthetic causal outcome gates."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import unittest
from test_v3_walkforward_quality_gate import candidate
from v3_five_area_frozen_backtest import (
    freeze_observed_row, five_area_scored_walkforward, WEIGHTS, ScoredBacktestError,
)


def row():
    return dict(code="000001",market="KOSPI",asof="2024-01-23",
        certified=False,official_score=None,available=100,unavailable=0,
        factors=dict(volume=12,technical=20,financial=16,valuation=15,momentum=10),
        factor_available=dict(WEIGHTS),earned=73)


class FiveAreaFrozenTests(unittest.TestCase):
    def test_current_model_runs_all_five_horizons_without_certification(self):
        p=candidate()
        r=row();r.update(code=p['case']['code'],market=p['case']['market'],asof=p['case']['signal_date'])
        s=freeze_observed_row(r,issuer_id=p['case']['issuer_id'],frozen_at_utc=r['asof']+'T07:00:00Z')
        p['frozen_score_snapshot']=s
        p['case']['evidence']['signal_snapshot_sha256']=s['snapshot_sha256']
        before=deepcopy(p)
        out=five_area_scored_walkforward([p],split_date='2026-01-01')
        self.assertEqual(out['score_model_weights'],WEIGHTS)
        self.assertEqual(len(out['horizons_trading_session_proxies']),5)
        self.assertFalse(out['industry_in_score'])
        self.assertFalse(out['partial_score_rescaled'])
        self.assertFalse(out['v3_release_ready'])
        self.assertEqual(p,before)
        p['frozen_score_snapshot']['factors']['volume']=13
        with self.assertRaises(ScoredBacktestError):
            five_area_scored_walkforward([p],split_date='2026-01-01')

    def test_missing_partial_legacy_nonfinite_and_inflated_totals_rejected(self):
        changes=[lambda r:r.update(available=95),
            lambda r:r['factor_available'].update(valuation=15),
            lambda r:r['factors'].update(industry=15),
            lambda r:r['factors'].update(volume=None),
            lambda r:r['factors'].update(volume=float('nan')),
            lambda r:r['factors'].update(volume=True),
            lambda r:r.update(earned=100),lambda r:r.update(certified=True)]
        for change in changes:
            r=row();change(r)
            with self.assertRaises(ScoredBacktestError):
                freeze_observed_row(r,issuer_id='00000001',frozen_at_utc=r['asof']+'T07:00:00Z')

    def test_actual_published_full_rows_freeze_without_partial_rescaling(self):
        docs=Path(__file__).resolve().parents[1]/'docs'
        summary=json.loads((docs/'v3-current-observed-all-listed-summary.json').read_text())
        fields={'volume':('거래량_OBV_잠정점수','거래량_OBV_산출배점'),
            'technical':('기술_잠정점수','기술_산출배점'),
            'financial':('재무_잠정점수','재무_산출배점'),
            'valuation':('밸류에이션_잠정점수','가치_산출배점'),
            'momentum':('모멘텀_잠정점수','모멘텀_산출배점')}
        count=0
        with (docs/'v3-current-observed-all-listed.csv').open(encoding='utf-8-sig') as f:
            for saved in csv.DictReader(f):
                if saved['산출가능배점_100점중']!='100':continue
                r=dict(code=saved['종목코드'],market=saved['시장'],asof=saved['가격자료기준일'],
                    certified=False,official_score=None,available=100,unavailable=0,
                    earned=float(saved['관측잠정합계_100점중']),
                    factors={k:float(saved[v[0]]) for k,v in fields.items()},
                    factor_available={k:int(saved[v[1]]) for k,v in fields.items()})
                # Identity is deliberately synthetic here: only actual score
                # shapes are tested, never an authenticated issuer/PIT claim.
                snapshot=freeze_observed_row(r,issuer_id='99999999',
                    frozen_at_utc=r['asof']+'T07:00:00Z')
                self.assertFalse(snapshot['historical_source_authenticated'])
                count+=1
        self.assertEqual(count,summary['provisional_complete_100point_observation_count'])
        # A date rollover may legitimately have zero complete source rows.
        # Count reconciliation above must still hold; never manufacture a
        # complete row or rescale partial evidence to satisfy this test.

    def test_real_zero_score_is_observed_and_dated_freeze_required(self):
        r=row();r['factors']={k:0 for k in WEIGHTS};r['earned']=0
        s=freeze_observed_row(r,issuer_id='00000001',frozen_at_utc=r['asof']+'T07:00:00Z')
        self.assertEqual(s['candidate_full_total_research_only'],0)
        for stamp in ('2024-01-24T07:00:00Z','2024-01-23T06:00:00Z'):
            with self.assertRaises(ScoredBacktestError):
                freeze_observed_row(r,issuer_id='00000001',frozen_at_utc=stamp)


if __name__=='__main__':unittest.main()
