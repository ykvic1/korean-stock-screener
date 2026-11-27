import unittest
from collections import Counter
from io import BytesIO
from zipfile import ZipFile
from v3_dart_whole_report_pilot import (
    number, parse_xml, inspect, queue, PilotError,
)

RECEIPT='20260316001465'

def doc(body):
    return ('<?xml version="1.0" encoding="UTF-8"?><DOCUMENT>'+body+'</DOCUMENT>').encode()

def table(rows):
    return '<TABLE><TBODY>'+''.join('<TR>'+''.join('<TD>'+c+'</TD>' for c in row)+'</TR>' for row in rows)+'</TBODY></TABLE>'

def archive(raw,name=RECEIPT+'.xml'):
    b=BytesIO()
    with ZipFile(b,'w') as z:z.writestr(name,raw)
    return b.getvalue()

class WholeReportPilotTest(unittest.TestCase):
    def test_legacy_korean_xml_encoding_and_failure_diagnostics(self):
        raw = doc('<TITLE>연결 현금흐름표</TITLE><P>(단위 : 원)</P>' +
                  table([['영업활동현금흐름', '100'], ['유형자산의 취득', '(20)']]))
        for label, codec in (('euc-kr', 'euc_kr'), ('windows-949', 'cp949')):
            encoded = raw.decode().replace('UTF-8', label).encode(codec)
            diagnostics = Counter()
            self.assertEqual(parse_xml(encoded, 'CFS', {'cfo': 100},
                                       diagnostics=diagnostics), {'ppe': 20})
            self.assertEqual(diagnostics['CF_matched_table'], 1)
            diagnostics.clear()
            self.assertEqual(parse_xml(encoded[:-1], 'CFS', {'cfo': 100},
                                       diagnostics=diagnostics), {})
            self.assertEqual(diagnostics, {'xml_rejected': 1, 'xml_error_unclosed_token': 1})

    def test_named_space_and_malformed_xml_are_distinguished(self):
        body = '<TITLE>연결 현금흐름표</TITLE><P>(단위 : 원)</P>'
        body += table([['영업활동현금흐름', '100'], ['유형자산의&nbsp;취득', '(20)']])
        raw = doc(body)
        diagnostics = Counter()
        result, status = inspect(archive(raw), RECEIPT, 'CFS',
                                 {'2025': {'cfo': 100}, '2024': {'cfo': 90}},
                                 diagnostics=diagnostics)
        self.assertEqual((result, status), ({'2025': {'ppe': 20}}, 'anchored_candidates'))
        self.assertEqual(diagnostics['xml_nbsp_retry'], 1)
        self.assertEqual(diagnostics['xml_parsed'], 1)
        for invalid, reason in ((raw.replace(b'&nbsp;', b'&unknown;'), 'undefined_entity'),
                                (doc('<P></TITLE>'), 'tag_mismatch')):
            diagnostics.clear()
            self.assertEqual(parse_xml(invalid, 'CFS', {'cfo': 100},
                                       diagnostics=diagnostics), {})
            self.assertEqual(diagnostics, {'xml_rejected': 1, 'xml_error_' + reason: 1})

    def test_numeric_zero_not_dash_or_blank(self):
        self.assertEqual(number('0'),0)
        self.assertEqual(number('(1,000)'),-1000)
        for x in ('-','—','','1.2','1,200원'):
            self.assertIsNone(number(x))

    def test_explicit_scope_units_and_multiple_periods(self):
        raw=doc('<TITLE>연결 현금흐름표</TITLE><P>(단위 : 원)</P>'+
                table([['영업활동현금흐름','100','90','80'],
                       ['유형자산의 취득','(20)','(15)','(10)'],
                       ['무형자산의 취득','0','-','(1)'],
                       ['투자활동현금흐름','(22)','(17)','(11)']]))
        result,status=inspect(archive(raw),RECEIPT,'CFS',
                              {'2025':{'cfo':100},'2024':{'cfo':90},'2023':{'cfo':80}})
        self.assertEqual(status,'anchored_candidates')
        self.assertEqual(result['2025']['intangible'],0)
        self.assertNotIn('intangible',result['2024'])
        self.assertEqual(result['2023']['ppe'],10)
        self.assertEqual(parse_xml(raw,'OFS',{'cfo':100}),{})
        self.assertEqual(parse_xml(raw,'CFS',{'cfo':101}),{})
        self.assertEqual(parse_xml(raw.replace('단위 : 원'.encode(),'단위 : 천원'.encode()),
                                   'CFS',{'cfo':100}),{})

    def test_single_cell_table_unit_overrides_section_unit(self):
        rows = [['영업활동현금흐름', '100'], ['유형자산의 취득', '(20)']]
        title = '<TITLE>연결 현금흐름표</TITLE>'
        krw_table = table([['(단위 : 원)'], *rows])
        self.assertEqual(parse_xml(doc(title + krw_table), 'CFS', {'cfo': 100}),
                         {'ppe': 20})

        thousand_table = table([['(단위 : 천원)'], *rows])
        conflict_table = table([['(단위 : 원)'], ['(단위 : 천원)'], *rows])
        for invalid_table in (thousand_table, conflict_table):
            raw = doc(title + '<P>(단위 : 원)</P>' + invalid_table)
            self.assertEqual(parse_xml(raw, 'CFS', {'cfo': 100}), {})

    def test_owner_profit_and_eps_require_two_anchors(self):
        raw=doc('<TITLE>연결 포괄손익계산서</TITLE><P>(단위 : 원)</P>'+
                table([['매출액','1000','900'],['영업이익','100','90'],
                       ['지배기업의 소유주에게 귀속되는 당기순이익','75','65'],
                       ['기본주당이익','15','13']]))
        self.assertEqual(parse_xml(raw,'CFS',{'revenue':1000,'operating_income':100}),
                         {'owners_profit':75,'eps':15})
        self.assertEqual(parse_xml(raw,'CFS',{'revenue':1000,'operating_income':90}),{})

    def test_owner_note_requires_explicit_scope_saved_eps_and_profit_reconciliation(self):
        title = '<P>34. 주당이익 (연결)</P><P>당기 (단위 : 원)</P>'
        rows = [['지배주주 당기순이익', '25,143,819,961'],
                ['가중평균유통보통주식수', '11,035,851'],
                ['기본주당이익', '2,278'], ['희석주당이익', '2,278']]
        anchors = {'reported_eps': 2278}
        self.assertEqual(parse_xml(doc(title + table(rows)), 'CFS', anchors),
                         {'owners_profit': 25143819961})
        self.assertEqual(parse_xml(doc(title + table(rows)), 'OFS', anchors), {})
        self.assertEqual(parse_xml(doc(title + table(rows)), 'CFS', {'reported_eps': 1987}), {})
        rows[0][1] = '25,100,000,000'
        self.assertEqual(parse_xml(doc(title + table(rows)), 'CFS', anchors), {})

    def test_ambiguous_column_or_label_and_attachment_rejected(self):
        raw=doc('<TITLE>연결 현금흐름표</TITLE><P>(단위 : 원)</P>'+
                table([['영업활동현금흐름','100','100'],['유형자산의 취득','(20)','(10)']]))
        self.assertEqual(parse_xml(raw,'CFS',{'cfo':100}),{})
        self.assertEqual(inspect(archive(raw,RECEIPT+'_00761.xml'),RECEIPT,'CFS',{}),
                         ({},'main_document_not_identified'))
        with self.assertRaises(PilotError):
            inspect(b'not zip',RECEIPT,'CFS',{})

    def test_receipt_selection_excludes_already_reviewed_and_complete(self):
        def single(code,available):
            return {'code':code,'available':available,'gaps':[
                {'area':'valuation','component':'per_eps_quality_growth'}]}
        p={'asof':'2026-11-26','single_component_queue':[
            single('210540',98.33),single('000001',59),single('000002',100)],
            'multiple_missing_index':[],'shape_names':[]}
        a={'year':2025,'currency':'KRW','fs_div':'CFS','receipt_id':RECEIPT,
           'values':{'revenue':1000,'operating_income':100,'owners_profit':None}}
        b={'records':[{'code':code,'annual':a} for code in ('210540','000001','000002')]}
        h={'records':{'210540':{'years':{'2025':{'CFS':{'receipt_id':RECEIPT,
             'values':{'revenue':1000,'operating_income':100}}}}}}}
        e={'records':{'210540':{'annual_receipt_id':RECEIPT,'fs_div':'CFS',
            'reported_basic_eps_2023_2025':[10,20,30]}}}
        q=queue(p,b,{'records':{}},{},h,e,{'records':{}})
        self.assertEqual(len(q),1)
        self.assertEqual(q[0]['wanted'],{'2025':['owners_profit']})
        self.assertEqual(queue(p,b,{'records':{}},{RECEIPT:{}},h,e,{'records':{}}),[])
        self.assertEqual(queue(p,b,{'records':{}},{},h,e,{'records':{
             '210540':{'annual_receipt_id':RECEIPT,'owners_profit_krw':75}}}),[])

if __name__=='__main__':unittest.main()
