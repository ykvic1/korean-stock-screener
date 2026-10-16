"""No network access, credentials or existing docs mutations in these tests."""
import json
import os
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from official_probe.clients import (KRXClient, DARTClient, ProbeError,
                                    canonical_date, code6)
from official_probe.validation import (inspect_krx, filter_visible_filings,
                                       public_safety_summary)
from official_probe.__main__ import run

ASOF = '2026-10-16'


def item(code='005930', bas='20261016', **updates):
    row = {'BAS_DD':bas,'ISU_CD':code,'ISU_NM':'TEST',
           'TDD_OPNPRC':'100','TDD_HGPRC':'120','TDD_LWPRC':'90',
           'TDD_CLSPRC':'110','ACC_TRDVOL':'1,500'}
    row.update(updates)
    return row


class Response:
    def __init__(self, data, status_code=200):
        self.content = data if isinstance(data,bytes) else json.dumps(data).encode()
        self.status_code = status_code


class CaptureSession:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url,kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def zip_mapping(xml=b'<result><list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list></result>'):
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('CORPCODE.xml',xml)
    return buffer.getvalue()


class ContractTest(unittest.TestCase):
    def test_stock_code_preserves_leading_zero(self):
        self.assertEqual(code6('005930'),'005930')
        with self.assertRaises(ValueError): code6('5930')

    def test_date_checks_calendar(self):
        self.assertEqual(canonical_date(ASOF),'20261016')
        for bad in ('20261016','2026-02-30','2026-13-01'):
            with self.subTest(bad=bad),self.assertRaises(ValueError): canonical_date(bad)

    def test_krx_auth_header_and_one_market_request(self):
        session = CaptureSession([Response({'OutBlock_1':[item()]})])
        rows, raw = KRXClient('TEST_KEY',session=session).daily_market('KOSPI',ASOF)
        self.assertEqual(rows[0]['ISU_CD'],'005930')
        self.assertEqual(len(session.calls),1)
        url, kw = session.calls[0]
        self.assertTrue(url.endswith('/sto/stk_bydd_trd'))
        self.assertNotIn('TEST_KEY',url)
        self.assertEqual(kw['headers'],{'AUTH_KEY':'TEST_KEY'})
        self.assertEqual(kw['params'],{'basDd':'20261016'})
        self.assertFalse(kw['allow_redirects'])
        self.assertEqual(raw, session.answers[0].content if session.answers else raw)

    def test_krx_kosdaq_endpoint_and_invalid_market(self):
        session = CaptureSession([Response({'OutBlock_1':[item('247540')]})])
        KRXClient('K',session=session).daily_market('KOSDAQ',ASOF)
        self.assertTrue(session.calls[0][0].endswith('/sto/ksq_bydd_trd'))
        with self.assertRaises(ValueError): KRXClient('K',session=session).daily_market('KONEX',ASOF)

    def test_krx_auth_failure_suppresses_request_details(self):
        secret='SENSITIVE_KEY_MUST_NOT_LEAK'
        session = CaptureSession([RuntimeError('https://site/?key='+secret)])
        with self.assertRaises(ProbeError) as e:
            KRXClient(secret,session=session).daily_market('KOSPI',ASOF)
        self.assertNotIn(secret,str(e.exception))

    def test_krx_redirect_and_empty_is_failure(self):
        for body,status in [({'OutBlock_1':[]},200),({},200),({},302)]:
            with self.subTest(status=status,body=body),self.assertRaises(ProbeError):
                KRXClient('K',session=CaptureSession([Response(body,status)])).daily_market('KOSPI',ASOF)

    def test_krx_ohlc_parse_and_unverified_vol(self):
        d = inspect_krx([item()], 'KOSPI',ASOF,{'005930'},b'official_payload')
        s = d['symbol_data']['005930']
        self.assertEqual(s['ohlc_observed']['close'],110)
        self.assertEqual(s['reported_acc_trdvol'],1500)
        self.assertIsNone(s['regular_volume'])
        self.assertFalse(s['confirmed_scoring_allowed'])
        self.assertEqual(s['price_session'],'UNVERIFIED')
        self.assertEqual(s['volume_session'],'UNVERIFIED')
        self.assertEqual(len(d['raw_source_sha256']),64)

    def test_missing_code_is_not_fabricated(self):
        d=inspect_krx([item()], 'KOSPI',ASOF,{'005930','000660'},b'raw')
        self.assertEqual(d['missing_symbols'],['000660'])
        self.assertNotIn('000660',d['symbol_data'])

    def test_duplicate_symbol_fails(self):
        with self.assertRaisesRegex(ProbeError,'Duplicate'):
            inspect_krx([item(),item()],'KOSPI',ASOF,{'005930'},b'raw')

    def test_wrong_date_fails(self):
        with self.assertRaisesRegex(ProbeError,'date'):
            inspect_krx([item(bas='20261015')],'KOSPI',ASOF,{'005930'},b'raw')

    def test_ohlc_range_fails_and_missing_price_fails(self):
        for row in [item(TDD_HGPRC='105'),item(TDD_CLSPRC='-'),item(TDD_OPNPRC='0')]:
            with self.subTest(row=row),self.assertRaises(ProbeError):
                inspect_krx([row],'KOSPI',ASOF,{'005930'},b'raw')

    def test_zero_volume_not_confirmed_regular_trades(self):
        d=inspect_krx([item(ACC_TRDVOL='0')],'KOSPI',ASOF,{'005930'},b'raw')
        self.assertEqual(d['symbol_data']['005930']['reported_acc_trdvol'],0)
        self.assertIsNone(d['symbol_data']['005930']['regular_volume'])

    def test_public_summary_does_not_include_extra_vendor_fields(self):
        d=public_safety_summary({'code':'005930','confirmed_scoring_allowed':False,'secret':'bad'})
        self.assertNotIn('secret',d)

    def test_dart_mapping_zip_and_url_parameter(self):
        session=CaptureSession([Response(zip_mapping())])
        m=DARTClient('PRIVATE',session=session).corp_mapping()
        self.assertEqual(m,{'005930':'00126380'})
        url, kw=session.calls[0]
        self.assertTrue(url.endswith('/corpCode.xml'))
        self.assertEqual(kw['params']['crtfc_key'],'PRIVATE')
        self.assertFalse(kw['allow_redirects'])

    def test_dart_mapping_rejects_bad_zip(self):
        with self.assertRaises(ProbeError):
            DARTClient('KEY',session=CaptureSession([Response(b'not_zip')])).corp_mapping()

    def test_dart_013_is_no_data_not_verified(self):
        session=CaptureSession([Response({'status':'013','message':'no data'})])
        result=DARTClient('K',session=session).recent_filings('00126380','2026-08-01',ASOF)
        self.assertEqual(result,[])

    def test_dart_error_redacts_secrets(self):
        secret='VERY_PRIVATE_0123'
        session=CaptureSession([RuntimeError('https://opendart.fss.or.kr/?crtfc_key='+secret)])
        with self.assertRaises(ProbeError) as e:
            DARTClient(secret,session=session).recent_filings('00126380','2026-08-01',ASOF)
        self.assertNotIn(secret,str(e.exception))

    def test_dart_asof_cutoff_excludes_same_day(self):
        rows=[{'rcept_dt':'20261015','rcept_no':'20261015000001','report_nm':'보고서'},
              {'rcept_dt':'20261016','rcept_no':'20261016000001','report_nm':'당일'},
              {'rcept_dt':'20261014','rcept_no':'bad','report_nm':'오류'}]
        out=filter_visible_filings(rows,ASOF)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['rcept_dt'],'20261015')

    def test_financial_rows_not_automatically_pit_verified(self):
        session=CaptureSession([Response({'status':'000','list':[{'account_nm':'매출액'}]})])
        d=DARTClient('KEY',session=session).annual_statement_sample('00126380',2025)
        self.assertEqual(d['rows'],1)
        self.assertFalse(d['asof_join_validated'])
        self.assertIsNone(d['metric_values'])

    def test_dry_run_requires_no_keys_and_makes_no_requests(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'data.json'
            source.write_text(json.dumps({'date':ASOF,'stocks':[
                {'code':'005930','market':'KOSPI'},{'code':'247540','market':'KOSDAQ'}]}),encoding='utf-8')
            output=Path(t)/'output.json'
            with patch.dict(os.environ, {'KRX_API_KEY':'unused','DART_API_KEY':'unused'}):
                result=run(['--asof',ASOF,'--source',str(source),'--output',str(output)])
            self.assertEqual(result['mode'],'dry_run')
            self.assertEqual(result['krx']['live_requests_if_authorized'],2)
            self.assertEqual(result['dart']['companies_planned'],2)
            self.assertFalse(result['confirmed_scoring_allowed'])
            self.assertTrue(output.exists())

    def test_live_without_keys_reports_key_required(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'data.json';output=Path(t)/'report.json'
            source.write_text(json.dumps({'date':ASOF,'stocks':[{'code':'005930','market':'KOSPI'}]}))
            with patch.dict(os.environ, {'KRX_API_KEY':'','DART_API_KEY':''}):
                result=run(['--asof',ASOF,'--source',str(source),'--output',str(output),'--live'])
            self.assertEqual(result['krx']['status'],'key_required')
            self.assertEqual(result['dart']['status'],'key_required')
            self.assertFalse(result['investment_data_ready'])

    def test_live_one_market_and_dart_mapping_with_mocks(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'data.json';output=Path(t)/'sample.json'
            source.write_text(json.dumps({'date':ASOF,'stocks':[{'code':'005930','market':'KOSPI'}]}))
            krx=CaptureSession([Response({'OutBlock_1':[item()]})])
            dart=CaptureSession([Response(zip_mapping()),Response({'status':'013'})])
            with patch.dict(os.environ, {'KRX_API_KEY':'KSECRET','DART_API_KEY':'DSECRET'}):
                r=run(['--asof',ASOF,'--source',str(source),'--output',str(output),'--live'],
                      krx_session=krx,dart_session=dart)
            self.assertEqual(len(krx.calls),1)
            self.assertEqual(len(dart.calls),2)
            self.assertEqual(r['krx']['status'],'observed_unverified')
            self.assertEqual(r['dart']['status'],'observed_unverified')
            self.assertFalse(r['confirmed_scoring_allowed'])
            report=output.read_text()
            self.assertNotIn('KSECRET',report)
            self.assertNotIn('DSECRET',report)

    def test_rejects_misidentified_market(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'data.json';source.write_text(json.dumps({'date':ASOF,'stocks':[{'code':'005930','market':'KOSPI'}]}))
            with self.assertRaises(ValueError):
                run(['--asof',ASOF,'--source',str(source),'--symbols','000660',
                     '--output',str(Path(t)/'out.json')])

    def test_live_does_not_expand_beyond_ten(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'data.json'
            stocks=[{'code':str(i).zfill(6),'market':'KOSPI'} for i in range(20)]
            source.write_text(json.dumps({'date':ASOF,'stocks':stocks}))
            with self.assertRaises(ValueError):
                run(['--asof',ASOF,'--source',str(source),'--symbols',','.join(s['code'] for s in stocks[:11]),
                     '--output',str(Path(t)/'out.json')])


if __name__ == '__main__':
    unittest.main()
