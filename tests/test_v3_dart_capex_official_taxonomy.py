"""Network-free guardrails for the official FSS cash-flow taxonomy audit."""
from unittest import TestCase

import v3_dart_capex_official_taxonomy as t


def entry(section="CF3", account="dart_PurchaseOfPropertyPlantAndEquipment",
          label="유형자산의 취득", data="X", **extra):
    result = {"sj_div": section, "account_id": account,
              "account_nm": label, "label_kor": label, "label_eng": "",
              "data_tp": data}
    result.update(extra)
    return result


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses[len(self.calls) - 1]


class OfficialTaxonomyTests(TestCase):
    def test_only_unambiguous_acquisition_cashflow_standard_ids(self):
        self.assertEqual(t.candidate_kind(entry(), "CF3"), "ppe")
        self.assertEqual(t.candidate_kind(
            entry(account="dart_PaymentForAcquisitionOfIntangibleAssets",
                  label="무형자산 취득"), "CF3"), "intangible")
        for value in (
            entry(section="IS1"),
            entry(data="Text"),
            entry(account="custom_PrivateAccount"),
            entry(account="dart_ProceedsFromSaleOfPropertyPlantAndEquipment"),
            entry(label="유형자산의 처분"),
            entry(label="무형자산 및 유형자산 취득"),
            entry(label="유형자산", account="dart_IncreaseInAssets"),
            entry(account="dart_PurchaseOfPropertyPlantAndEquipment",
                  label="기타 현금흐름"),
            entry(label="<redacted>", label_eng=5),
        ):
            self.assertIsNone(t.candidate_kind(value, "CF3"))

    def test_four_official_sections_exactly_and_conflicts_eliminated(self):
        shared = "dart_PaymentForAcquisitionOfAssets"
        pay_ppe = entry(account="dart_PurchaseOfPropertyPlantAndEquipment")
        pay_intangible = entry(account="dart_PurchaseOfIntangibleAssets",
                               label="무형자산의 취득")
        responses = []
        for section, lines in zip(t.SECTIONS, (
            [dict(pay_ppe, sj_div="CF1"), dict(pay_intangible, sj_div="CF1"),
             entry(section="CF1", account=shared, label="유형자산 취득")],
            [entry(section="CF2", account=shared, label="무형자산 취득")],
            [dict(pay_ppe, sj_div="CF3")],
            [dict(pay_intangible, sj_div="CF4")])):
            responses.append(Reply({"status": "000", "list": lines}))
        session = Session(responses)
        ids = t.official_candidates("a" * 40, session)
        self.assertEqual(len(session.calls), 4)
        self.assertEqual(ids["ppe"], {pay_ppe["account_id"]})
        self.assertEqual(ids["intangible"], {pay_intangible["account_id"]})
        self.assertNotIn(shared, ids["ppe"])
        self.assertNotIn(shared, ids["intangible"])
        for (url, kwargs), section in zip(session.calls, t.SECTIONS):
            self.assertEqual(url, "https://opendart.fss.or.kr/api/xbrlTaxonomy.json")
            self.assertEqual(kwargs["params"]["sj_div"], section)

    def test_existing_ifrs_whitelist_is_not_a_novel_account(self):
        found = {"ppe": {
            "dart_PurchaseOfPropertyPlantAndEquipment",
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
        }, "intangible": {"dart_PurchaseOfIntangibleAssets"}}
        self.assertTrue(t.novel_account_id(
            "dart_PurchaseOfPropertyPlantAndEquipment", "ppe", found))
        self.assertFalse(t.novel_account_id(
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "ppe", found))
        self.assertFalse(t.novel_account_id(
            "dart_PurchaseOfIntangibleAssets", "ppe", found))
        self.assertFalse(t.novel_account_id(None, "ppe", found))

    def test_failed_provider_response_cannot_echo_secret_or_raw_source(self):
        class ExplodingSession:
            def get(self, url, **kwargs):
                raise RuntimeError("token-should-never-enter-diagnostics")
        with self.assertRaises(t.OfficialTaxonomyError) as caught:
            t.official_candidates("x" * 40, ExplodingSession())
        self.assertEqual(str(caught.exception), "official_taxonomy_request_failed")
        self.assertNotIn("token", str(caught.exception))
        self.assertRaises(t.OfficialTaxonomyError,
                          t.official_candidates, "short", ExplodingSession())
        bad = Session([Reply({"status": "013", "message": "private", "list": []})])
        with self.assertRaises(t.OfficialTaxonomyError) as err:
            t.official_candidates("x" * 40, bad)
        self.assertEqual(str(err.exception), "official_taxonomy_invalid_response")
