from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from v3_forward_factor_validation import frozen_score, summarize, factor_value
from v3_score_observation_capture import SCORE_PATH, VERSION


class FrozenFactorValidationTests(unittest.TestCase):
    def test_full_zero_and_partial_are_distinct(self):
        self.assertEqual(factor_value([0,6,'2026-11-13'],6,'2026-11-13'),'low')
        self.assertEqual(factor_value([3,6,'2026-11-13'],6,'2026-11-13'),'high')
        self.assertEqual(factor_value([2,3,'2026-11-13'],6,'2026-11-13'),'partial')
        self.assertEqual(factor_value([6,6,'2026-11-14'],6,'2026-11-13'),'missing')

    def test_frozen_source_and_same_market_samples(self):
        day='2026-11-13'
        rows={}
        members={}
        for i,part in enumerate(([6,6,day],[0,6,day],[1,3,day],None),1):
            code=f'{i:06}'
            row={'name':code,'market':'KOSPI','score':50,'rank':i,'available':90,
                 'asof':day,'oldest_asof':day,'parts':{'volume':{}}}
            if part is not None:
                row['parts']['volume']['long_horizon_growth']=part
            rows[code]=row
            members[code]=[row[k] for k in ('name','market','score','rank','available','asof','oldest_asof')]+[None]
        report={'version':VERSION,'asof':day,'rows':rows}
        raw=json.dumps(report).encode()
        observation={'model_version':VERSION,'score_asof':day,'source_score_sha256':sha256(raw).hexdigest(),
                     'source_commit':'a'*40,'rows':members}
        with TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/SCORE_PATH
            path.parent.mkdir()
            path.write_bytes(raw)
            self.assertEqual(frozen_score(root,observation),report)
            changed=dict(observation,rows={**members,'000001':['Changed']+members['000001'][1:]})
            self.assertIsNone(frozen_score(root,changed))
        openings={c:['official_open_observed',100] for c in members}
        output=summarize(observation,openings,{'000001':8,'000002':-4},report)
        m=output['factors']['volume.long_horizon_growth']['markets']['KOSPI']
        self.assertEqual(m['high']['observed_mean_return_pct'],8)
        self.assertEqual(m['low']['observed_mean_return_pct'],-4)
        self.assertEqual(m['partial_weight_count'],1)
        self.assertEqual(m['missing_weight_count'],1)
        self.assertIsNone(m['observed_mean_return_spread_pct_points'])
        self.assertEqual(summarize(observation,openings,{},None)['status'],'frozen_factor_source_unavailable')
