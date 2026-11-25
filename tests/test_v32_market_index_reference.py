"""Index summaries never infer values from stocks or make provider calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from v32_market_index_reference import build, write, SOURCE


class IndexReferenceTests(unittest.TestCase):
    def test_same_day_and_partial_observation(self):
        report = build('2026-11-24', {
            'KOSPI': {'date':'2026-11-24','r20':3.123456,'r60':-2,'r120':None},
            'KOSDAQ': {'date':'2026-11-23','r20':10,'r60':20,'r120':30},
            'OTHER': {'date':'2026-11-24','r20':1}})
        self.assertEqual(report['markets'], {'KOSPI': {
            'asof':'2026-11-24','return_pct':{'20':3.1235,'60':-2.0,'120':None}}})
        self.assertEqual(report['source'], SOURCE)
        self.assertFalse(report['official_krx_index_certified'])
        self.assertEqual(report['new_provider_calls'],0)

    def test_invalid_values_stay_missing(self):
        for values in (None, {}, {'KOSPI':{'date':'2026-11-24','r20':True,
                'r60':float('nan'),'r120':-100}}, {'KOSDAQ':{'date':'2026-11-25','r20':5}}):
            self.assertEqual(build('2026-11-24',values)['markets'],{})
        with self.assertRaises(ValueError):
            build('2026-02-30',{})

    def test_write_replaces_prior_snapshot_instead_of_relabeling(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'v32-market-index-reference.json'
            write(path,'2026-11-23',{'KOSPI':{'date':'2026-11-23','r20':0}})
            write(path,'2026-11-24',{})
            self.assertEqual(json.loads(path.read_text())['markets'],{})
            self.assertEqual(json.loads(path.read_text())['asof'],'2026-11-24')
            self.assertFalse(path.with_suffix('.json.tmp').exists())


if __name__=='__main__':
    unittest.main()
