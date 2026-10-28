"""Offline XBRL original-file structure pilot safety and issuer isolation."""
import io
import json
import unittest
import zipfile

import v3_dart_bulk_history as bulk
import v3_dart_cashflow_enrichment as cash
import v3_dart_xbrl_capex_pilot as pilot
from test_v3_dart_cashflow_enrichment import live_inputs


IFRS_NS = "http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"
INSTANCE_NS = "http://www.xbrl.org/2003/instance"


def archive(xml, name="report.xbrl"):
    handle = io.BytesIO()
    with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr(name, xml)
    return handle.getvalue()


def sample_xml(*, namespace=IFRS_NS, fact="PurchaseOfIntangibleAssets",
               attributes='contextRef="C" unitRef="KRW"'):
    return (
        '<xbrl xmlns="' + INSTANCE_NS + '" xmlns:ifrs="' + namespace + '">'
        '<ifrs:' + fact + ' ' + attributes + '>10</ifrs:' + fact + '>'
        '</xbrl>'
    ).encode("utf8")


class Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index:index + chunk_size]


class Session:
    def __init__(self, body):
        self.body = body
        self.requests = []

    def get(self, url, *, params, timeout, stream):
        self.requests.append((url, params, timeout, stream))
        return Response(self.body)


class OriginalXbrlEvidenceTests(unittest.TestCase):
    def fixture(self):
        market, catalog, major = live_inputs()
        asof, members = cash.asof_members(market, catalog, major)
        code = sorted(members)[0]
        receipt = major["records"][code]["years"]["2025"]["CFS"]["receipt_id"]
        observation = {
            "year": 2025, "scope": "CFS", "annual_receipt_id": receipt,
            "major_annual_receipt_matched": True,
            "operating_cashflow_krw": 100,
            "ppe_capex_krw": 20, "intangible_capex_krw": None,
            "total_capex_krw": None, "free_cashflow_krw": None,
            "raw_source_account_rows_published": False,
            "historical_PIT_certified": False,
        }
        records = {code: {"scope": "CFS", "years": {"2025": observation}}}
        state = cash.summary(asof, members, records, cash.fingerprint(major))
        return market, catalog, major, state, code, receipt

    def test_live_tag_only_is_aggregate_without_issuer_or_amount(self):
        market, catalog, major, state, code, receipt = self.fixture()
        session = Session(archive(sample_xml()))
        result = pilot.run_pilot(
            market, catalog, major, state, mode="live",
            key="A" * 40, session=session)
        self.assertEqual(result["provider_xbrl_file_calls"], 1)
        self.assertEqual(result["valid_zip_files"], 1)
        self.assertEqual(result["xbrl_instances_detected"], 1)
        self.assertEqual(result["reports_with_any_missing_source_tag"], 1)
        self.assertEqual(result["reports_with_ifrs_intangible_tag_candidate"], 1)
        self.assertTrue(result["no_xbrl_amounts_or_investment_scores_derived"])
        self.assertTrue(result["no_period_or_scope_or_krw_fact_verified"])
        self.assertEqual(len(session.requests), 1)
        url, params, timeout, stream = session.requests[0]
        self.assertTrue(url.endswith("/api/fnlttXbrl.xml"))
        self.assertEqual(params["rcept_no"], receipt)
        self.assertEqual(params["reprt_code"], "11011")
        self.assertTrue(stream)
        public = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(code, public)
        self.assertNotIn(receipt, public)
        self.assertNotIn("PurchaseOfIntangibleAssets", public)

    def test_dry_run_no_provider_calls_and_only_four_maximum(self):
        market, catalog, major, state, _, _ = self.fixture()
        report = pilot.run_pilot(market, catalog, major, state, mode="dry-run")
        self.assertEqual(report["eligible_sampled_issuer_years"], 1)
        self.assertEqual(report["provider_xbrl_file_calls"], 0)
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, major, state, mode="live")
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.select_candidates(state, major, asof=report["asof"], limit=5)

    def test_missing_source_tag_must_be_exact_ifrs_and_fact_reference(self):
        for xml in (
            sample_xml(namespace="http://company.example/custom"),
            sample_xml(fact="PurchasesOfOtherAssets"),
            sample_xml(attributes='contextRef="C"'),
        ):
            result = pilot.inspect_binary_archive(archive(xml))
            self.assertEqual(result["instances"], 1)
            self.assertNotIn("intangible", result["facts"])

    def test_file_absent_xml_is_not_misclassified_as_zip(self):
        result = pilot.inspect_binary_archive(
            b'<result><status>014</status><message>no file</message></result>')
        self.assertTrue(result["file_absent"])
        self.assertEqual(result["instances"], 0)
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.inspect_binary_archive(
                b'<result><status>010</status><message>secret</message></result>')

    def test_zip_extraction_and_entity_protection(self):
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.inspect_binary_archive(archive(sample_xml(), "../../secret.xml"))
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.inspect_binary_archive(archive(b"<!DOCTYPE xbrl []><xbrl/>"))
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.inspect_binary_archive(archive(b"A" * 200000))

    def test_changed_major_receipt_cannot_be_probed(self):
        market, catalog, major, state, code, _ = self.fixture()
        state["records"][code]["years"]["2025"]["annual_receipt_id"] = "20260401000001"
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, major, state, mode="dry-run")


if __name__ == "__main__":
    unittest.main()
