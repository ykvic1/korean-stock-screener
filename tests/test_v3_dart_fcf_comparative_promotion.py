import copy
import unittest

import v3_dart_cashflow_enrichment as cf
import v3_dart_fcf_prior_comparative_probe as research
import v3_dart_fcf_comparative_promotion as promo
from test_v3_dart_cashflow_enrichment import live_inputs

CODE = '053620'
PRIOR = {
    'year': 2024, 'scope': 'CFS', 'annual_receipt_id': '20250318000001',
    'major_annual_receipt_matched': True, 'operating_cashflow_krw': 100,
    'ppe_capex_krw': 20, 'intangible_capex_krw': None,
    'total_capex_krw': None, 'free_cashflow_krw': None,
    'raw_source_account_rows_published': False, 'historical_PIT_certified': False,
}
LATEST = {
    'year': 2025, 'scope': 'CFS', 'annual_receipt_id': '20260318000001',
    'major_annual_receipt_matched': True, 'operating_cashflow_krw': 150,
    'ppe_capex_krw': 25, 'intangible_capex_krw': 5,
    'total_capex_krw': 30, 'free_cashflow_krw': 120,
    'raw_source_account_rows_published': False, 'historical_PIT_certified': False,
}


def inputs():
    prior = copy.deepcopy(PRIOR)
    later = copy.deepcopy(LATEST)
    year2023 = copy.deepcopy(PRIOR)
    year2023.update({'year': 2023, 'annual_receipt_id': '20240318000001',
                     'intangible_capex_krw': 5, 'total_capex_krw': 25,
                     'free_cashflow_krw': 75})
    cash = {'records': {CODE: {'scope': 'CFS', 'years': {
        '2023': year2023, '2024': prior, '2025': later}}}}
    source = research.make_record(CODE, later, [('2024', prior)],
        research={'2024': {'reason': 'complete_comparative_candidate',
                           'candidate_parts': {'intangible_capex_krw': 0}}},
        archive_hash='a' * 64, profile_hash='b' * 64, day='2026-11-06')
    state = {'schema': research.SCHEMA, 'scores_unchanged': True,
             'records': {CODE: source}}
    return cash, state


class PromotionTests(unittest.TestCase):
    def test_only_explicit_zero_ppe_pair_and_cfo_is_promoted(self):
        cash, state = inputs()
        updated, codes = promo.apply(cash, state)
        self.assertEqual(codes, [CODE])
        before = cash['records'][CODE]['years']['2024']
        after = updated['records'][CODE]['years']['2024']
        self.assertIsNone(before['intangible_capex_krw'])
        self.assertEqual(after['intangible_capex_krw'], 0)
        self.assertEqual(after['total_capex_krw'], 20)
        self.assertEqual(after['free_cashflow_krw'], 80)
        self.assertTrue(promo.verify_promoted_year(after, updated['records'][CODE]['years'], CODE, '2024'))
        self.assertFalse(after['historical_PIT_certified'])
        snapshot = cf.summary('2026-11-06', {CODE: ('태양', 'KOSDAQ')},
                              updated['records'], 'a' * 64)
        self.assertEqual(snapshot['metrics']['three_year_fcf_receipt_matched_issuers'], 1)
        self.assertEqual(snapshot['metrics']['later_original_comparative_years'], 1)

    def test_tampering_with_amount_or_source_breaks_verification(self):
        cash, state = inputs()
        updated, _ = promo.apply(cash, state)
        for kind in ('amount', 'source', 'scope', 'anchor'):
            changed = copy.deepcopy(updated['records'][CODE])
            if kind == 'amount':
                changed['years']['2024']['ppe_capex_krw'] = 99
            elif kind == 'source':
                changed['years']['2025']['annual_receipt_id'] = '20260319000002'
            else:
                if kind == 'scope':
                    changed['years']['2024'][promo.PROOF]['scope'] = 'OFS'
                else:
                    changed['years']['2025']['major_annual_receipt_matched'] = False
            with self.assertRaises(promo.ComparativePromotionError):
                promo.verify_promoted_year(changed['years']['2024'], changed['years'], CODE, '2024')

    def test_source_revisions_revoke_only_later_supplied_amount(self):
        cash, state = inputs()
        updated, _ = promo.apply(cash, state)
        z = updated['records'][CODE]['years']['2024']
        same = promo.restore_if_later_receipt_changed(
            z, actual_later_receipt='20260318000001')
        self.assertIs(same, z)
        back = promo.restore_if_later_receipt_changed(
            z, actual_later_receipt='20260319000002')
        self.assertEqual(back['ppe_capex_krw'], 20)
        self.assertIsNone(back['intangible_capex_krw'])
        self.assertIsNone(back['free_cashflow_krw'])
        self.assertNotIn(promo.PROOF, back)
        self.assertEqual(back['operating_cashflow_krw'], 100)

    def test_missing_source_overlap_or_conflict_never_promotes(self):
        cash, state = inputs()
        for key, value in [('scope', 'OFS'), ('fy2025_receipt', '20260319000002')]:
            altered = copy.deepcopy(state)
            altered['records'][CODE][key] = value
            with self.assertRaises((promo.ComparativePromotionError, research.ComparativeError)):
                promo.apply(cash, altered)
        source = copy.deepcopy(state)
        source['records'][CODE]['years']['2024']['reason'] = 'no_additional_comparative_capex'
        with self.assertRaises((promo.ComparativePromotionError, research.ComparativeError)):
            promo.apply(cash, source)

    def test_all_required_missing_years_must_be_complete(self):
        cash, state = inputs()
        year2023 = copy.deepcopy(PRIOR)
        year2023['year'] = 2023
        year2023['annual_receipt_id'] = '20240318000001'
        cash['records'][CODE]['years']['2023'] = year2023
        # Single 2024 research record cannot silently certify a missing 2023.
        self.assertEqual(promo.apply(cash, state)[1], [])

    def test_revised_later_receipt_rolls_back_comparative_only(self):
        market, catalog, bulk = live_inputs()
        cash, _ = inputs()
        code = '005930'
        entry = cash['records'].pop(CODE)
        cash['records'][code] = entry
        before = entry['years']['2024']
        source = research.make_record(code, entry['years']['2025'], [('2024', before)],
            research={'2024': {'reason': 'complete_comparative_candidate',
                               'candidate_parts': {'intangible_capex_krw': 0}}},
            archive_hash='a' * 64, profile_hash='b' * 64, day=catalog['date'])
        amended, chosen = promo.apply(cash, {'schema': research.SCHEMA,
            'scores_unchanged': True, 'records': {code: source}})
        self.assertEqual(chosen, [code])
        day, members = cf.asof_members(market, catalog, bulk)
        old = cf.summary(day, members, amended['records'], cf.fingerprint(bulk))
        self.assertTrue(cf.verify_state(old, market, catalog, bulk))
        revised = copy.deepcopy(bulk)
        revised['records'][code]['years']['2025']['CFS']['receipt_id'] = '20260319000002'
        updated = cf.retained_current_market_cashflow(market, catalog, revised, old)
        y = updated['records'][code]['years']
        self.assertNotIn('2025', y)
        self.assertNotIn(promo.PROOF, y['2024'])
        self.assertEqual(y['2024']['ppe_capex_krw'], 20)
        self.assertIsNone(y['2024']['intangible_capex_krw'])
        self.assertEqual(y['2023']['free_cashflow_krw'], 75)


if __name__ == '__main__':
    unittest.main()
