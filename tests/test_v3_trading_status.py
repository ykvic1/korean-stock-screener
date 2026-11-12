"""Trading-status display only; never modifies score or active universe."""
import copy
import json
import tempfile
from pathlib import Path
import unittest

from v3_trading_status import build, main


def catalog(day, volume_by_code):
    rows=[]
    for code in ('000001', '000002', '000003'):
        rows.append({'code':code,'name':'시험'+code,'market':'KOSPI',
                     'listing_zero_volume_reference':volume_by_code.get(code),
                     'price_history_status':'price_source_or_validation_error'})
    return {'schema':'v22-current-listed-catalog-0.1','date':day,
            'listed_total':len(rows),'rows':rows}


class TradingStatusTests(unittest.TestCase):
    def test_mixed_code_halt_is_display_only_and_roster_change_resets_streak(self):
        prior = build(catalog('2026-11-09', {'000001': False}))
        current = catalog('2026-11-10', {'000001': False})
        current["rows"][2]["code"] = "00A8Z0"
        current["rows"][2]["name"] = "혼합종목코드"
        current["rows"][2]["listing_zero_volume_reference"] = True
        status = build(current, prior)
        self.assertEqual(status["listed_total"], 3)
        self.assertIn("00A8Z0", status["rows"])
        self.assertEqual(status["rows"]["00A8Z0"]["state"], "zero_volume")
        self.assertIsNone(status["rows"]["00A8Z0"]["observed_zero_streak"])
        self.assertEqual(status["rows"]["00A8Z0"]["duration"], "unknown")
        self.assertFalse(status["rows"]["00A8Z0"]["official_reason_verified"])
        self.assertEqual(status["score_points_added"], 0)
        self.assertEqual(build(current, status), status)
        for invalid in ("00a8Z0", "00A8_0", "００A８Z０"):
            malformed = copy.deepcopy(current)
            malformed["rows"][2]["code"] = invalid
            with self.assertRaisesRegex(ValueError, "invalid_catalog_issuer"):
                build(malformed)

    def test_unproven_long_halt_has_unknown_duration(self):
        report=build(catalog('2026-11-09',{'000001':True,'000002':True,'000003':False}))
        self.assertEqual(report['listed_total'],3)
        self.assertEqual(report['counts']['zero_volume'],2)
        self.assertEqual(report['counts']['unknown_halt_duration'],2)
        self.assertIsNone(report['rows']['000001']['observed_zero_streak'])
        self.assertFalse(report['krx_regular_session_verified'])
        self.assertEqual(report['score_points_added'],0)
        self.assertFalse(report['rows']['000001']['official_reason_verified'])

    def test_short_two_days_then_extended_then_auto_resumption(self):
        initial=build(catalog('2026-11-06',{'000001':False,'000002':True}))
        first=build(catalog('2026-11-09',{'000001':True,'000002':True}),initial)
        second=build(catalog('2026-11-10',{'000001':True,'000002':True}),first)
        third=build(catalog('2026-11-11',{'000001':True,'000002':True}),second)
        self.assertEqual(first['rows']['000001']['observed_zero_streak'],1)
        self.assertEqual(second['rows']['000001']['observed_zero_streak'],2)
        self.assertEqual(third['rows']['000001']['observed_zero_streak'],3)
        self.assertEqual(second['rows']['000001']['duration'],'short_1_to_2')
        self.assertEqual(third['rows']['000001']['duration'],'extended_3_plus')
        self.assertEqual(third['rows']['000002']['duration'],'unknown')
        resumed=build(catalog('2026-11-12',{'000001':False,'000002':True}),third)
        self.assertNotIn('000001',resumed['rows'],
                         'an absent code means normal positive-volume trading')
        self.assertEqual(resumed['counts']['trade_observed'],1)

    def test_missing_source_session_does_not_invent_two_day_halt(self):
        first=build(catalog('2026-11-09',{'000001':False}))
        skipped=build(catalog('2026-11-11',{'000001':True}),first)
        self.assertIsNone(skipped['rows']['000001']['observed_zero_streak'])
        self.assertEqual(skipped['rows']['000001']['duration'],'unknown')

    def test_identity_change_and_same_day_runs(self):
        prev=build(catalog('2026-11-09',{'000001':False}))
        current=catalog('2026-11-10',{'000001':True})
        first=build(current,prev)
        self.assertEqual(build(current,first),first)
        renamed=copy.deepcopy(current)
        renamed['rows'][0]['name']='변경된명칭'
        self.assertEqual(build(renamed,first)['rows']['000001']['duration'],'unknown')

    def test_invalid_flags_do_not_turn_into_zero_volume(self):
        invalid=catalog('2026-11-09',{'000001':0})
        with self.assertRaises(ValueError): build(invalid)
        invalid=catalog('2026-11-09',{})
        invalid['rows'].append(copy.deepcopy(invalid['rows'][0]))
        invalid['listed_total']+=1
        with self.assertRaises(ValueError): build(invalid)

    def test_idempotent_write_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            docs=Path(directory)
            (docs/'v22-market-catalog.json').write_text(json.dumps(
                catalog('2026-11-09',{'000001':True})),encoding='utf8')
            main(['--mode','write','--docs',str(docs)])
            original=(docs/'v3-trading-status.json').read_bytes()
            main(['--mode','verify','--docs',str(docs)])
            main(['--mode','write','--docs',str(docs)])
            self.assertEqual(original,(docs/'v3-trading-status.json').read_bytes())


if __name__=='__main__': unittest.main()
