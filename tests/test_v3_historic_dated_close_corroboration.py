"""Focused offline tests of historical KRX/FDR count-only corroboration."""
import json
import unittest
from unittest.mock import patch
import pandas as pd
import v3_historic_dated_close_corroboration as a

DAYS=['2026-11-10','2026-11-09','2026-11-06']
CODES=[str(100000+i) for i in range(1500)]


class Client:
    def __init__(self): self.calls=[]
    def daily_market(self, market, day):
        self.calls.append((market,day))
        if market=='KOSPI':
            return ([{'ISU_CD':code,'TDD_CLSPRC':'1000'} for code in CODES],b'example-private')
        return [],b'example-private'


def loader(day):
    return pd.DataFrame({'Code':CODES,'Market':['KOSPI']*len(CODES),
                         'Close':[1000]*len(CODES)}),'a'*64


def members(frame,day):
    return {code:{'code':code,'market':'KOSPI'} for code in CODES}


class HistoricCloseAuditTests(unittest.TestCase):
    def test_three_dates_exact_joins_without_source_price_or_ticker_export(self):
        client=Client()
        with patch.object(a,'classify_row',return_value=('valid_numeric_observation',None)):
            report=a.audit(DAYS,client=client,loader=loader,
                           membership=members,
                           validate_source=lambda *args,**kwargs:None)
        self.assertEqual(len(client.calls),6)
        self.assertEqual([x['asof'] for x in report['historical_sessions']],DAYS)
        self.assertEqual([x['counts']['exact_close'] for x in report['historical_sessions']],[1500]*3)
        self.assertEqual(report['source_requests'],9)
        self.assertFalse(report['historical_260_session_corroborated'])
        self.assertFalse(report['investment_score_or_available_weight_changed'])
        self.assertFalse(report['source_prices_or_identifiers_exported'])
        encoded=json.dumps(report)
        self.assertNotIn('100000',encoded)
        self.assertNotIn('example-private',encoded)
        self.assertNotIn('TDD_CLSPRC',encoded)

    def test_one_historical_price_disagreement_does_not_get_promoted(self):
        client=Client()
        def divergent(day):
            frame,sha=loader(day)
            if day==DAYS[1]:frame.loc[0,'Close']=999
            return frame,sha
        with patch.object(a,'classify_row',return_value=('valid_numeric_observation',None)):
            report=a.audit(DAYS,client=client,loader=divergent,
                           membership=members,
                           validate_source=lambda *args,**kwargs:None)
        self.assertEqual([x['counts']['different_close'] for x in report['historical_sessions']],[0,1,0])
        self.assertFalse(report['historical_sessions'][1]['full_day_close_matches'])
        self.assertFalse(report['regular_only_full_ohlcv_verified'])

    def test_invalid_or_duplicate_dates_block_before_provider_requests(self):
        for dates in (DAYS[:2],[DAYS[0]]*3,['2026-11-06','2026-11-09','2026-11-10'],['2026-02-30']+DAYS[:2]):
            client=Client()
            with self.assertRaises(a.HistoricalCloseAuditError):
                a.audit(dates,client=client,loader=loader,membership=members)
            self.assertEqual(client.calls,[])


if __name__=='__main__': unittest.main()
