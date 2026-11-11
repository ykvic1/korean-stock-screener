"""KRX close crosscheck never silently changes market evidence or scores."""
import json
import unittest

from v3_krx_saved_close_crosscheck import check, CrosscheckError
from v3_krx_history_collection import HistoryCollectionError

DAY = '2026-11-10'


def item(code, close, *, day=DAY):
    return {'ISU_CD':code, 'BAS_DD':day.replace('-',''),
            'TDD_OPNPRC':str(close), 'TDD_HGPRC':str(close+5),
            'TDD_LWPRC':str(close-5), 'TDD_CLSPRC':str(close),
            'ACC_TRDVOL':'100'}


class Provider:
    def __init__(self, *, wrong_day=False):
        self.calls=[]
        self.rows={'KOSPI':[item('000001',100),item('000002',220)],
                   'KOSDAQ':[item('000003',300,day='2026-11-09' if wrong_day else DAY)]}

    def daily_market(self, market, day):
        self.calls.append((market,day))
        rows=self.rows[market]
        return rows,json.dumps({'OutBlock_1':rows},ensure_ascii=False).encode()


def saved():
    return {'date':DAY,'candidate_count':3,'stocks':[
        {'code':'000001','market':'KOSPI','price':100,'price_date':DAY},
        {'code':'000002','market':'KOSPI','price':200,'price_date':DAY},
        {'code':'000003','market':'KOSDAQ','price':300,'price_date':DAY},
    ]}


class SavedCloseSourceTests(unittest.TestCase):
    def test_all_market_and_priority_are_counted_without_ticker_export(self):
        provider=Provider()
        report=check(saved(),['000001'],['000002'],provider,
                     snapshot_sha256='a'*64)
        self.assertEqual(provider.calls,[('KOSPI',DAY),('KOSDAQ',DAY)])
        self.assertEqual(report['counts']['saved_market']['exact_close'],2)
        self.assertEqual(report['counts']['saved_market']['different_close'],1)
        self.assertEqual(report['counts']['below90_ma_priority']['exact_close'],1)
        self.assertEqual(report['counts']['ninety_plus_ma_priority']['different_close'],1)
        self.assertEqual(report['added_available_weight'],0)
        self.assertIs(report['krx_regular_only_volume_verified'],False)
        text=json.dumps(report)
        for code in ('000001','000002','000003'):
            self.assertNotIn(code,text)
        self.assertNotIn('TDD_CLSPRC',text)

    def test_absent_code_never_becomes_zero_or_valid_close(self):
        provider=Provider()
        provider.rows['KOSDAQ']=[item('000004',350)]
        report=check(saved(),['000003'],[],provider,snapshot_sha256='b'*64)
        self.assertEqual(report['counts']['below90_ma_priority']['absent_in_krx'],1)
        self.assertEqual(report['counts']['saved_market']['exact_close'],1)
        self.assertEqual(report['counts']['saved_market']['different_close'],1)

    def test_cross_date_response_fails_whole_batch(self):
        with self.assertRaises(HistoryCollectionError):
            check(saved(),['000001'],[],Provider(wrong_day=True),
                  snapshot_sha256='c'*64)

    def test_priority_or_snapshot_identity_not_silently_relaxed(self):
        for first,second in [(['000001','000001'],[]),
                             (['000001'],['000001']),
                             (['999999'],[])]:
            with self.assertRaises(CrosscheckError):
                check(saved(),first,second,Provider(),
                      snapshot_sha256='d'*64)


if __name__ == '__main__':
    unittest.main()
