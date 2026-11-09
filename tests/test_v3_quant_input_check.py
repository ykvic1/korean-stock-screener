import copy
import unittest
from v3_quant_input_check import validate


def sample():
    return {'status':'success','date':'2026-11-06','candidate_count':2,'stocks':[
        {'code':'000001','name':'one','market':'KOSPI','price':10,'price_date':'2026-11-06'},
        {'code':'000002','name':'two','market':'KOSDAQ','price':None,'price_date':None}]}


class QuantInputCheckTests(unittest.TestCase):
    def test_missing_prices_and_dated_retained_rows_are_reported_without_filling(self):
        source=sample();source['stocks'][0]['price_date']='2026-11-05';before=copy.deepcopy(source)
        result=validate(source)
        self.assertEqual(result['missing_or_unpriced'],1)
        self.assertEqual(result['retained_older_price_rows'],1)
        self.assertEqual(source,before);self.assertEqual(result['provider_calls'],0)

    def test_duplicate_wrong_identity_and_count_mismatch_block_before_collection(self):
        for case in ('duplicate','count','code','market','status'):
            source=sample()
            if case=='duplicate':source['stocks'][1]['code']='000001'
            if case=='count':source['candidate_count']=3
            if case=='code':source['stocks'][1]['code']='bad'
            if case=='market':source['stocks'][1]['market']='UNKNOWN'
            if case=='status':source['status']='partial'
            with self.subTest(case=case),self.assertRaises(ValueError):validate(source)

    def test_future_undated_and_malformed_price_cannot_enter_research(self):
        for changes in ({'price_date':'2026-11-07'},{'price_date':None},{'price_date':'2026-02-30'},
                        {'price':float('nan')},{'price':True},{'price':-1}):
            source=sample();source['stocks'][0].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate(source)
