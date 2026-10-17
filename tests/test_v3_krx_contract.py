"""KRX daily OHLC and ACC_TRDVOL must NEVER become regular-session data."""
import copy
import unittest

from official_probe.validation import inspect_krx
from v3_krx_contract import (
    normalize_daily_observation, check_no_session_promotion, SCHEMA,
)


def make_krx():
    raw=[{
        'ISU_CD':'005930', 'BAS_DD':'20261016',
        'TDD_OPNPRC':'75,000', 'TDD_HGPRC':'75,900',
        'TDD_LWPRC':'74,800', 'TDD_CLSPRC':'75,400',
        'ACC_TRDVOL':'15,000,000',
    }]
    return inspect_krx(raw, 'KOSPI', '2026-10-16',
                       {'005930'}, b'krx-daily-test-payload')


class V3KRXContractTests(unittest.TestCase):
    def test_official_ohlc_preserved_but_aggregate_volume_not_promoted(self):
        x=normalize_daily_observation(make_krx(),'005930')
        self.assertEqual(x['schema'],SCHEMA)
        self.assertEqual(x['ohlc_observed']['close'],75400)
        self.assertEqual(x['aggregate_acc_trdvol_observed'],15000000)
        self.assertIsNone(x['regular_only_volume'])
        self.assertEqual(x['price_session'],'UNVERIFIED')
        self.assertEqual(x['volume_session'],'UNVERIFIED')
        self.assertFalse(x['aggregate_volume_session_certified_regular_only'])
        self.assertFalse(x['technical_indicators_eligible'])
        self.assertFalse(x['confirmed_scoring_allowed'])
        self.assertIsNone(x['confirmed_score'])
        self.assertTrue(check_no_session_promotion(x))

    def test_cannot_smuggle_session_confirmation_through_source(self):
        original=make_krx()
        cases=('volume_verified','regular_volume','price_session',
               'scoring_allowed','wrong_source_hash','wrong_session_flag',
               'wrong_date','negative_volume','wrong_ohlc','missing_symbol')
        for case in cases:
            x=copy.deepcopy(original)
            row=x['symbol_data']['005930']
            if case=='volume_verified':x['volume_session_validated']=True
            if case=='regular_volume':row['regular_volume']=14000000
            if case=='price_session':row['price_session']='KRX_REGULAR_0900_1530_ASIA_SEOUL'
            if case=='scoring_allowed':row['confirmed_scoring_allowed']=True
            if case=='wrong_source_hash':x['raw_source_sha256']='untrusted'
            if case=='wrong_session_flag':x['price_session_validated']=True
            if case=='wrong_date':row['trade_date']='2026-10-15'
            if case=='negative_volume':row['reported_acc_trdvol']=-1
            if case=='wrong_ohlc':row['ohlc_observed']['high']=1
            if case=='missing_symbol':x['symbol_data']={}
            with self.subTest(case=case),self.assertRaises(ValueError):
                normalize_daily_observation(x,'005930')

    def test_normalized_data_cannot_be_modified_into_tradable_bar(self):
        row=normalize_daily_observation(make_krx(),'005930')
        for field,value in (
            ('regular_only_volume',14000000),
            ('price_session','KRX_REGULAR_0900_1530_ASIA_SEOUL'),
            ('volume_session','KRX_REGULAR_0900_1530_ASIA_SEOUL'),
            ('technical_indicators_eligible',True),
            ('confirmed_scoring_allowed',True),
            ('confirmed_score',99),
        ):
            altered=copy.deepcopy(row)
            altered[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                check_no_session_promotion(altered)

    def test_official_validation_rejects_wrong_date_and_numeric_ohlc(self):
        rows=[{
            'ISU_CD':'005930','BAS_DD':'20261015',
            'TDD_OPNPRC':'100','TDD_HGPRC':'101',
            'TDD_LWPRC':'99','TDD_CLSPRC':'100',
            'ACC_TRDVOL':'200',
        }]
        from official_probe.clients import ProbeError
        with self.assertRaises(ProbeError):
            inspect_krx(rows,'KOSPI','2026-10-16',{'005930'},b'bad')
        rows[0]['BAS_DD']='20261016'
        rows[0]['TDD_CLSPRC']='999'
        with self.assertRaises(ProbeError):
            inspect_krx(rows,'KOSPI','2026-10-16',{'005930'},b'bad')


if __name__=='__main__':
    unittest.main()
