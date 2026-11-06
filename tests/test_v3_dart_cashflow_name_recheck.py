"""Offline exact-name CAPEX repair tests; no credentials or provider calls."""
import copy
import unittest

import v3_dart_cashflow_enrichment as cf
from test_v3_dart_cashflow_enrichment import (
    live_inputs, FakeSession, FakeResponse, rows)
from test_v3_dart_bulk_history import CORPS


def nonstandard(year, code, label="무형자산의 취득", amount="100000000",
                account_id=cf.NONSTANDARD_ACCOUNT_ID, receipt=None,
                scope="CFS", currency="KRW"):
    return {"corp_code":CORPS[code],"stock_code":code,"bsns_year":str(year),
            "reprt_code":"11011","sj_div":"CF","account_detail":"-",
            "fs_div":scope,"rcept_no":receipt or f"{year+1}0318000001",
            "currency":currency,"account_id":account_id,
            "account_nm":label,"thstrm_amount":amount}


class ExactNameSession(FakeSession):
    def __init__(self,*,variants=None):
        super().__init__()
        self.variants=variants or []

    def get(self,url,params,timeout):
        if url.endswith("corpCode.xml"):
            return super().get(url,params,timeout)
        self.calls.append((url,params))
        year=int(params["bsns_year"])
        code={v:k for k,v in CORPS.items()}[params["corp_code"]]
        result=rows(year,code,skip_intangible=True)
        result.extend(nonstandard(year,code,**kw) for kw in self.variants)
        return FakeResponse({"status":"000","list":result})


class NameRecheckTests(unittest.TestCase):
    def incomplete(self):
        market,catalog,bulk=live_inputs()
        prior,calls=cf.collect(market,catalog,bulk,None,
            key="synthetic-non-sensitive-DART-api-key",
            session=FakeSession(missing_capex=2024),issuer_limit=5)
        self.assertEqual(calls,16)
        self.assertEqual(prior["metrics"]["fcf_company_years"],10)
        return market,catalog,bulk,prior

    def test_exact_nonstandard_cf_name_can_recover_one_missing_component_once(self):
        market,catalog,bulk,prior=self.incomplete()
        _,_,_,chosen=cf.plan(market,catalog,bulk,prior,limit=5,
                             recover_missing_names=True)
        self.assertEqual(len(chosen),5)
        self.assertTrue(all(row[3]==[2024] for row in chosen))
        sess=ExactNameSession(variants=[{}])
        saved,api=cf.collect(market,catalog,bulk,prior,
            key="synthetic-non-sensitive-DART-api-key",session=sess,
            issuer_limit=5,recover_missing_names=True)
        self.assertEqual(api,6)
        self.assertEqual(saved["metrics"]["fcf_company_years"],15)
        self.assertEqual(saved["metrics"]["three_year_fcf_receipt_matched_issuers"],5)
        self.assertEqual(saved["metrics"]["name_recheck_company_years"],5)
        self.assertEqual(saved["metrics"]["name_recheck_enriched_company_years"],5)
        self.assertEqual(saved["metrics"]["nonstandard_capex_name_components"],5)
        self.assertEqual(saved["source"],cf.SOURCE_WITH_NAMES)
        self.assertTrue(cf.verify_state(saved,market,catalog,bulk))
        for code in saved["records"]:
            self.assertEqual(saved["records"][code]["years"]["2024"]["free_cashflow_krw"],800000000)
        again=ExactNameSession(variants=[{}])
        repeated,n=cf.collect(market,catalog,bulk,saved,
            key="synthetic-non-sensitive-DART-api-key",session=again,
            issuer_limit=5,recover_missing_names=True)
        self.assertEqual((n,again.calls), (0,[]))
        self.assertEqual(repeated,saved)

    def test_reconciled_single_factor_fcf_priority_beats_arbitrary_code_order(self):
        market,catalog,bulk,prior=self.incomplete()
        baseline=cf.plan(market,catalog,bulk,prior,limit=2,
                         recover_missing_names=True)[3]
        high=sorted(set(prior["records"])-{r[1] for r in baseline})[-1]
        chosen=cf.plan(market,catalog,bulk,prior,limit=2,
            recover_missing_names=True,priority_codes={high})[3]
        self.assertEqual(chosen[0][1],high)
        self.assertEqual(len(chosen),2)
        self.assertTrue(all(row[3]==[2024] for row in chosen))
        with self.assertRaises(cf.CashflowError):
            cf.plan(market,catalog,bulk,prior,limit=2,
                    recover_missing_names=True,priority_codes={'999999'})

    def test_nonstandard_name_not_used_in_initial_exact_ifrs_collector(self):
        year=2024;code="005930"
        entries=rows(year,code,skip_intangible=True)+[nonstandard(year,code)]
        normal=cf.normalize_year(entries,code=code,corp=CORPS[code],year=year,
            scope="CFS",asof="2026-10-19",major_receipt=f"{year+1}0318000001")
        self.assertIsNone(normal["intangible_capex_krw"])
        restored=cf.normalize_year(entries,code=code,corp=CORPS[code],year=year,
            scope="CFS",asof="2026-10-19",major_receipt=f"{year+1}0318000001",
            allow_nonstandard_names=True)
        self.assertEqual(restored["intangible_capex_krw"],100000000)
        self.assertEqual(restored["source_account_name_fallback"],["intangible"])

    def test_conflicting_or_untrusted_labels_never_fill_missing_values(self):
        year=2024;code="005930"
        base=rows(year,code,skip_intangible=True)
        variants=[
            [nonstandard(year,code,account_id="dart_CustomIntangiblePurchase")],
            [nonstandard(year,code,label="무형자산 취득")],
            [nonstandard(year,code,amount="-100")],
            [nonstandard(year,code,amount="100"),
             nonstandard(year,code,amount="200")],
        ]
        for extras in variants:
            with self.subTest(extras=extras):
                found=cf.normalize_year(base+extras,code=code,corp=CORPS[code],
                    year=year,scope="CFS",asof="2026-10-19",
                    major_receipt=f"{year+1}0318000001",
                    allow_nonstandard_names=True)
                self.assertIsNone(found["intangible_capex_krw"])
                self.assertIsNone(found["free_cashflow_krw"])
                self.assertNotIn("source_account_name_fallback",found)
        # A contradictory standard ID cannot be made valid by supplying one
        # matching Korean custom account name alongside it.
        conflict=base+[dict(base[-1],thstrm_amount="400000000")]
        conflict+=[dict(base[-1],thstrm_amount="500000000")]
        conflict+=[nonstandard(year,code)]
        found=cf.normalize_year(conflict,code=code,corp=CORPS[code],
            year=year,scope="CFS",asof="2026-10-19",
            major_receipt=f"{year+1}0318000001",
            allow_nonstandard_names=True)
        self.assertEqual(found["ppe_capex_krw"],None)

    def test_mixed_receipts_scopes_and_foreign_currency_are_fail_closed(self):
        year=2024;code="005930"
        base=rows(year,code,skip_intangible=True)
        for extras in (
            [nonstandard(year,code,receipt="20250325000002")],
            [nonstandard(year,code,scope="OFS")],
        ):
            with self.assertRaises(cf.CashflowError):
                cf.normalize_year(base+extras,code=code,corp=CORPS[code],
                    year=year,scope="CFS",asof="2026-10-19",
                    major_receipt=f"{year+1}0318000001",
                    allow_nonstandard_names=True)
        ignored=cf.normalize_year(base+[nonstandard(year,code,currency="USD")],
            code=code,corp=CORPS[code],year=year,scope="CFS",asof="2026-10-19",
            major_receipt=f"{year+1}0318000001",allow_nonstandard_names=True)
        self.assertIsNone(ignored["intangible_capex_krw"])
        self.assertNotIn("source_account_name_fallback",ignored)

    def test_no_account_year_is_marked_checked_without_zero_or_repeat(self):
        market,catalog,bulk,prior=self.incomplete()
        sess=ExactNameSession()
        resumed,calls=cf.collect(market,catalog,bulk,prior,
            key="synthetic-non-sensitive-DART-api-key",session=sess,
            issuer_limit=5,recover_missing_names=True)
        self.assertEqual(calls,6)
        self.assertEqual(resumed["metrics"]["fcf_company_years"],10)
        self.assertEqual(resumed["metrics"]["name_recheck_enriched_company_years"],0)
        self.assertEqual(resumed["source"],cf.SOURCE_IFRS)
        self.assertTrue(cf.verify_state(resumed,market,catalog,bulk))
        self.assertFalse(cf.plan(market,catalog,bulk,resumed,limit=5,
                                 recover_missing_names=True)[3])

    def test_existing_facts_conflict_and_probe_provenance_tampering_are_rejected(self):
        market,catalog,bulk,prior=self.incomplete()
        first=prior["records"]["005930"]["years"]["2024"]
        altered=copy.deepcopy(first)
        new=copy.deepcopy(first);new["operating_cashflow_krw"]+=1
        with self.assertRaisesRegex(cf.CashflowError,"original_amount_conflict"):
            cf.merge_account_name_recheck(altered,new)
        new=copy.deepcopy(first);new["annual_receipt_id"]="20250325000002"
        with self.assertRaisesRegex(cf.CashflowError,"receipt_or_scope_mismatch"):
            cf.merge_account_name_recheck(altered,new)
        restored,_=cf.collect(market,catalog,bulk,prior,
            key="synthetic-non-sensitive-DART-api-key",
            session=ExactNameSession(variants=[{}]),issuer_limit=5,
            recover_missing_names=True)
        tampered=copy.deepcopy(restored)
        tampered["records"]["005930"]["years"]["2024"]["account_name_recheck"]["receipt_id"]="20250325000002"
        with self.assertRaises(cf.CashflowError):
            cf.verify_state(tampered,market,catalog,bulk)

    def test_receipt_bound_recheck_survives_new_market_day_but_revisions_quarantine(self):
        market,catalog,bulk,prior=self.incomplete()
        verified,_=cf.collect(market,catalog,bulk,prior,
            key="synthetic-non-sensitive-DART-api-key",
            session=ExactNameSession(variants=[{}]),issuer_limit=5,
            recover_missing_names=True)
        later_market=copy.deepcopy(market)
        later_catalog=copy.deepcopy(catalog)
        later_bulk=copy.deepcopy(bulk)
        later_market["date"]=later_catalog["date"]=later_bulk["asof"]="2026-10-20"
        for stock in later_market["stocks"]:stock["price_date"]="2026-10-20"
        carried=cf.retained_current_market_cashflow(
            later_market,later_catalog,later_bulk,verified)
        self.assertTrue(cf.verify_state(carried,later_market,later_catalog,later_bulk))
        self.assertFalse(cf.plan(later_market,later_catalog,later_bulk,carried,
                                 limit=5,recover_missing_names=True)[3])
        revised=copy.deepcopy(later_bulk)
        revised["records"]["005930"]["years"]["2024"]["CFS"]["receipt_id"]="20250325000002"
        quarantined=cf.retained_current_market_cashflow(
            later_market,later_catalog,revised,verified)
        self.assertNotIn("2024",quarantined["records"]["005930"]["years"])
        newqueue=cf.plan(later_market,later_catalog,revised,quarantined,
                         limit=5,recover_missing_names=True)[3]
        self.assertTrue(any(item[1]=="005930" and 2024 in item[3] for item in newqueue))


if __name__=="__main__":unittest.main()
