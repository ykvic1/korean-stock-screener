import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from official_probe.clients import KRXClient, KRXProbeError
from v3_historical_rights_source_probe import LISTINGS, evaluate, probe


class HistoricalRightsSourceProbeTests(unittest.TestCase):
    def test_fixed_cases_match_unpriced_historical_cohort(self):
        feed = Path(__file__).resolve().parents[1] / "docs/v3-historical-price-paths-20251113.json"
        self.assertEqual(set(LISTINGS), set(json.loads(feed.read_text())["unpriced_paid_rights"]["12"]))

    def test_client_uses_separate_certificate_market(self):
        session = Mock()
        response = session.get.return_value
        response.status_code = 200
        response.content = b'{"OutBlock_1":[{"ISU_CD":"J0098301G"}]}'
        client = KRXClient("fixture", session=session)
        self.assertEqual(client.subscription_rights("2026-07-06"), [{"ISU_CD": "J0098301G"}])
        self.assertEqual(session.get.call_args.args, ("https://data-dbg.krx.co.kr/svc/apis/sto/sr_bydd_trd",))
        self.assertEqual(session.get.call_args.kwargs["params"], {"basDd": "20260706"})
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        with self.assertRaises(ValueError):
            client.daily_market("subscription_rights", "2026-07-06")

    def test_requires_unique_certificate_correct_day_and_actual_trading(self):
        code = "009830"
        isin, day, _ = LISTINGS[code]
        row = {"ISU_CD": "J0098301G", "TARSTK_ISU_SRT_CD": "A009830",
               "BAS_DD": "20260706", "TDD_CLSPRC": "8,710", "ACC_TRDVOL": "350"}
        self.assertEqual(evaluate([row], code, isin, day), "verified_traded_close")
        self.assertEqual(evaluate([row, {**row, "ISU_CD": isin}], code, isin, day), "duplicate_certificate")
        self.assertEqual(evaluate([{**row, "ISU_CD": "J0098302G"}], code, isin, day), "certificate_not_returned")
        for field, value, expected in [
            ("BAS_DD", "20260707", "certificate_identity_or_date_mismatch"),
            ("TARSTK_ISU_SRT_CD", "009835", "certificate_identity_or_date_mismatch"),
            ("TDD_CLSPRC", "-", "no_verified_traded_close"),
            ("ACC_TRDVOL", "0", "no_verified_traded_close"),
        ]:
            with self.subTest(field=field):
                self.assertEqual(evaluate([{**row, field: value}], code, isin, day), expected)

    def test_denied_access_stops_with_one_request_and_aggregate_only(self):
        client = Mock()
        client.subscription_rights.side_effect = KRXProbeError("subscription_rights", "http_forbidden", 403)
        self.assertEqual(probe(client), {
            "planned_certificates": 9, "requests_attempted": 1,
            "outcomes": {"http_forbidden": 1}, "raw_prices_retained": False,
            "performance_validated": False,
        })
        client.subscription_rights.assert_called_once()
