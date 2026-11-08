import copy
import unittest
import v3_eps_basis_audit as a
from test_v3_dart_xbrl_capex_pilot import archive

NS='http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'
RECEIPTS={str(y):f'{y+1}0318000001' for y in (2023,2024,2025)}
EPS={'2023':-10.,'2024':0.,'2025':30.}
CFO={'2023':100,'2024':200,'2025':300}


def xml(year,*,direct=False,shares='100.25',share_unit='x:shares',identity='00123456'):
    years=[y for y in (2023,2024,2025) if y<=year]
    contexts=''.join(f'<context id="C{y}"><entity><identifier scheme="http://dart.fss.or.kr">{identity}</identifier></entity><period><startDate>{y}-01-01</startDate><endDate>{y}-12-31</endDate></period><scenario><d:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:ConsolidatedMember</d:explicitMember></scenario></context>' for y in years)
    facts=''.join(f'<i:CashFlowsFromUsedInOperatingActivities contextRef="C{y}" unitRef="W">{CFO[str(y)]}</i:CashFlowsFromUsedInOperatingActivities>' for y in years)
    facts+=''.join(f'<i:BasicEarningsLossPerShare contextRef="C{y}" unitRef="E">{EPS[str(y)]}</i:BasicEarningsLossPerShare>' for y in years if direct or year!=2025 or y!=2023)
    if year>=2024 and shares is not None:facts+=f'<i:WeightedAverageNumberOfOrdinarySharesOutstanding contextRef="C2024" unitRef="S" decimals="INF">{shares}</i:WeightedAverageNumberOfOrdinarySharesOutstanding>'
    return f'<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:x="http://www.xbrl.org/2003/instance" xmlns:i="{NS}" xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:iso="http://www.xbrl.org/2003/iso4217">{contexts}<unit id="W"><measure>iso:KRW</measure></unit><unit id="E"><divide><unitNumerator><measure>iso:KRW</measure></unitNumerator><unitDenominator><measure>x:shares</measure></unitDenominator></divide></unit><unit id="S"><measure>{share_unit}</measure></unit>{facts}</xbrl>'.encode()


def payload(year):
    common={'corp_code':'00123456','stock_code':'000001','fs_div':'CFS','bsns_year':str(year),'reprt_code':'11011','rcept_no':RECEIPTS[str(year)],'currency':'KRW','account_detail':'-'}
    return {'status':'000','list':[{**common,'sj_div':'CF','account_id':'ifrs-full_CashFlowsFromUsedInOperatingActivities','thstrm_amount':str(CFO[str(year)])},
        {**common,'sj_div':'IS','account_id':'ifrs-full_BasicEarningsLossPerShare','thstrm_amount':str(EPS[str(year)])}]}


def context():
    return {'code':'000001','scope':'CFS','receipts':RECEIPTS.copy(),'input_digest':'a'*64,
        'baseline':{'annual_receipt_id':RECEIPTS['2025'],'reported_basic_eps_2023_2025':[None,0.,30.]},
        'research':{'receipt_2024':RECEIPTS['2024'],'candidate_eps_2023':-10.,'cfo_2024_krw':200},'cash':{}}


class Reply:
    def __init__(self,body=None,raw=None):self.body=body;self.raw=raw
    def json(self):return copy.deepcopy(self.body)
    def iter_content(self,chunk_size):yield self.raw
    def close(self):pass


class Provider:
    def __init__(self,direct=False):
        self.api={str(y):payload(y) for y in (2023,2024,2025)}
        self.files={str(y):archive(xml(y,direct=direct)) for y in (2023,2024,2025)};self.calls=[]
    def __call__(self,endpoint,params,stream=False):
        self.calls.append((endpoint,copy.deepcopy(params)))
        if endpoint=='fnlttSinglAcntAll.json':return Reply(body=self.api[params['bsns_year']])
        if endpoint=='company.json':return Reply(body={'status':'000','corp_code':'00123456','stock_code':'000001'})
        return Reply(raw=self.files[next(y for y,r in RECEIPTS.items() if r==params['rcept_no'])])


def assess(provider,ctx=None,cached=None):
    return a.assess(ctx or context(),corp='00123456',day='2026-11-06',get=provider,cached=cached)


def record(evidence,ctx=None):
    return a.make_record(ctx or context(),day='2026-11-06',checked_on='2026-11-09',reason='same_filing_eps_completed' if evidence['path']=='same_filing' else 'basis_checks_passed',evidence=evidence)


class EPSBasisAuditTests(unittest.TestCase):
    def test_exact_noninteger_weighted_shares_latest_three_receipts_and_zero_overlap(self):
        evidence=assess(Provider());self.assertEqual(evidence['weighted_shares_2024'],{'2024':'100.25','2025':'100.25'})
        self.assertEqual(evidence['eps'],[-10.,0.,30.]);self.assertTrue(a.verify_record(record(evidence)))
        self.assertTrue(a.verify_for_context(record(evidence),context()))

    def test_equal_zero_eps_does_not_hide_share_basis_change(self):
        p=Provider();p.files['2025']=archive(xml(2025,shares='200.5'))
        with self.assertRaisesRegex(a.Blocked,'weighted_ordinary_shares_conflict'):assess(p)

    def test_cashflow_comparative_conflicts_and_missing_basis_are_rejected(self):
        p=Provider();p.files['2025']=archive(xml(2025).replace(b'unitRef="W">200<',b'unitRef="W">201<'))
        with self.assertRaisesRegex(a.Blocked,'comparative_cfo_conflict'):assess(p)
        p=Provider();p.files['2024']=archive(xml(2024,shares=None))
        with self.assertRaisesRegex(a.Blocked,'weighted_ordinary_shares_missing'):assess(p)

    def test_spot_shares_diluted_counts_wrong_units_and_zero_are_not_basic_weighted_shares(self):
        for raw in (xml(2024,share_unit='iso:KRW'),xml(2024).replace(b'WeightedAverageNumberOfOrdinarySharesOutstanding',b'NumberOfOrdinarySharesOutstanding'),xml(2024).replace(b'WeightedAverageNumberOfOrdinarySharesOutstanding',b'WeightedAverageNumberOfDilutedSharesOutstanding')):
            p=Provider();p.files['2024']=archive(raw)
            with self.assertRaisesRegex(a.Blocked,'weighted_ordinary_shares_missing'):assess(p)
        for value in ('0','-1','NaN'):
            self.assertIsNone(a.share_number(value))
        self.assertEqual(a.share_number('00100.2500'),'100.25')

    def test_changed_latest_receipt_or_latest_2023_eps_cannot_be_spliced(self):
        p=Provider();p.api['2023']['list'][0]['rcept_no']='20240401000001'
        with self.assertRaisesRegex(a.Blocked,'latest_receipt_changed'):assess(p)
        p=Provider();p.api['2023']['list'][1]['thstrm_amount']='-9'
        with self.assertRaisesRegex(a.Blocked,'latest_2023_eps_conflict'):assess(p)

    def test_coarse_or_undeclared_share_accuracy_cannot_mask_a_basis_change(self):
        for accuracy in ('decimals="-3"','decimals="0"','precision="4"',''):
            p=Provider();p.files['2024']=archive(xml(2024).replace(b'decimals="INF"',accuracy.encode()))
            with self.assertRaisesRegex(a.Blocked,'weighted_ordinary_shares_missing'):assess(p)

    def test_workflow_writes_audit_only_and_continues_after_verified_progress(self):
        from pathlib import Path
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/v3-eps-basis-audit.yml').read_text()
        self.assertIn('git add docs/v3-eps-basis-audit.json',text)
        self.assertNotIn('git add docs/v3-dart-reported-eps.json',text)
        self.assertNotIn('v3-operational-research-score.yml/dispatches',text)
        self.assertIn('head_repository.full_name == github.repository',text)
        self.assertIn("steps.collect.outputs.reviewed != '0'",text)
        self.assertLess(text.index('Verify and publish valid audit'),text.index('Surface interruption'))

    def test_latest_2023_original_fallback_requires_same_cfo_eps_scope_and_receipt(self):
        p=Provider();p.api['2023']['list']=p.api['2023']['list'][:1]
        evidence=assess(p);self.assertEqual(evidence['latest_2023_channel'],'original')
        self.assertTrue(a.verify_record(record(evidence)))
        p.files['2023']=archive(xml(2023).replace(b'>-10.0<',b'>-9<'))
        with self.assertRaisesRegex(a.Blocked,'latest_2023_eps_unavailable'):assess(p)

    def test_full_same_filing_eps_is_preferred_without_cross_filing_inference(self):
        p=Provider(direct=True);p.api['2023']={'status':'020'}
        e=assess(p);self.assertEqual(e['path'],'same_filing');self.assertTrue(a.verify_record(record(e)))
        self.assertEqual([x[1].get('bsns_year') for x in p.calls if x[0]=='fnlttSinglAcntAll.json'],['2025'])

    def test_cache_rechecks_latest_annual_receipts_but_does_not_refetch_unchanged_originals(self):
        p=Provider();saved=record(assess(p));again=Provider();e=assess(again,cached=saved)
        self.assertTrue(a.verify_record(record(e)))
        self.assertEqual(len(again.calls),3);self.assertTrue(all(x[0]=='fnlttSinglAcntAll.json' for x in again.calls))
        again.api['2023']['list'][0]['rcept_no']='20240401000001'
        with self.assertRaisesRegex(a.Blocked,'latest_receipt_changed'):assess(again,cached=saved)

    def test_direct_cache_and_current_original_both_preserve_saved_2024_2025(self):
        saved=record(assess(Provider(direct=True)));p=Provider(direct=True)
        self.assertEqual(assess(p,cached=saved)['eps'],[-10.,0.,30.]);self.assertEqual(len(p.calls),1)
        p=Provider();p.files['2025']=archive(xml(2025).replace(b'>30.0<',b'>31<'))
        with self.assertRaisesRegex(a.Blocked,'original_eps_unavailable'):assess(p)

    def test_future_receipts_identity_and_provider_errors_are_not_observations(self):
        c=context();c['receipts']['2025']='20261110000001'
        with self.assertRaisesRegex(a.Blocked,'expected_receipt_not_current'):assess(Provider(),c)
        p=Provider();p.api['2025']={'status':'020'}
        with self.assertRaises(a.SourceUnavailable):assess(p)
        p=Provider();p.api['2025']['list'][0]['corp_code']='00999999'
        with self.assertRaises(a.SourceUnavailable):assess(p)

    def test_proof_tampering_cannot_be_authorized_by_self_reported_flags(self):
        saved=record(assess(Provider()))
        for key,value in (('weighted_shares_2024',{'2024':'100.25','2025':'200.5'}),('weighted_shares_2024',{'2024':None,'2025':None}),('latest_2023_eps',-9.)):
            bad=copy.deepcopy(saved);bad['evidence'][key]=value
            bad['digest']=a.cf.fingerprint({k:v for k,v in bad.items() if k!='digest'})
            with self.assertRaises(a.AuditError):a.verify_record(bad)
        c=context();c['input_digest']='b'*64;self.assertFalse(a.verify_for_context(saved,c))

    def test_blocked_reviews_retain_no_unverified_values_and_never_authorize_scores(self):
        saved=a.make_record(context(),day='2026-11-06',checked_on='2026-11-09',reason='weighted_ordinary_shares_missing')
        self.assertFalse(a.verify_record(saved));self.assertTrue(saved['scores_unchanged']);self.assertFalse(saved['corporate_actions_certified'])
        bad=copy.deepcopy(saved);bad['evidence']={'eps':[1,2,3]};bad['digest']=a.cf.fingerprint({k:v for k,v in bad.items() if k!='digest'})
        with self.assertRaises(a.AuditError):a.verify_record(bad)


if __name__=='__main__':unittest.main()
