"""Offline XBRL original-file structure pilot safety and issuer isolation."""
import io
from datetime import date, timedelta
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
        self.assertTrue(result["no_statement_scope_verified"])
        self.assertTrue(result["annual_krw_facts_not_scored"])
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



    def test_annual_krw_context_proof_does_not_pretend_scope_is_verified(self):
        xml = (
            '<xbrl xmlns="' + INSTANCE_NS + '" xmlns:ifrs="' + IFRS_NS + '">'
            '<context id="C"><entity><identifier scheme="synthetic">X</identifier>'
            '</entity><period><startDate>2025-01-01</startDate>'
            '<endDate>2025-12-31</endDate></period></context>'
            '<unit id="KRW"><measure>iso4217:KRW</measure></unit>'
            '<ifrs:PurchaseOfIntangibleAssets contextRef="C" unitRef="KRW">'
            '12345</ifrs:PurchaseOfIntangibleAssets></xbrl>'
        ).encode("utf8")
        matched = pilot.inspect_binary_archive(archive(xml), year=2025)
        self.assertIn("intangible", matched["annual_krw_facts"])
        self.assertNotIn("ppe", matched["annual_krw_facts"])
        older = pilot.inspect_binary_archive(archive(xml), year=2024)
        self.assertEqual(older["annual_krw_facts"], set())
        usd = xml.replace(b"iso4217:KRW", b"iso4217:USD")
        self.assertEqual(
            pilot.inspect_binary_archive(archive(usd), year=2025)["annual_krw_facts"],
            set())
        segmented = xml.replace(
            b"</entity>", b"<segment><other>dimension</other></segment></entity>")
        self.assertEqual(
            pilot.inspect_binary_archive(archive(segmented), year=2025)[
                "annual_krw_facts"], set())

    def test_recent_prior_market_major_archive_defers_with_zero_provider_calls(self):
        market, catalog, major, state, code, receipt = self.fixture()
        asof, members = cash.asof_members(market, catalog, major)
        older = json.loads(json.dumps(major))
        older["asof"] = (date.fromisoformat(asof) - timedelta(days=1)).isoformat()
        session = Session(archive(sample_xml()))
        report = pilot.run_pilot(
            market, catalog, older, state, mode="live", key="A" * 40,
            session=session)
        self.assertEqual(report["source_readiness"],
                         "awaiting_same_day_annual_archive")
        self.assertEqual(report["eligible_sampled_issuer_years"], 0)
        self.assertEqual(report["provider_xbrl_file_calls"], 0)
        self.assertEqual(session.requests, [])
        future = json.loads(json.dumps(major))
        future["asof"] = (date.fromisoformat(asof) + timedelta(days=1)).isoformat()
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, future, state, mode="dry-run")

    def test_verified_recent_archive_original_research_runs_without_same_day_score(self):
        market, catalog, major, current, code, receipt = self.fixture()
        asof, members = cash.asof_members(market, catalog, major)
        older = json.loads(json.dumps(major))
        older["asof"] = (date.fromisoformat(asof) - timedelta(days=1)).isoformat()
        verified = cash.summary(
            older["asof"], members, current["records"], cash.fingerprint(older))
        untouched = json.dumps(verified, sort_keys=True)
        session = Session(archive(sample_xml()))
        report = pilot.run_pilot(
            market, catalog, older, verified, mode="live",
            key="A" * 40, session=session)
        self.assertEqual(report["source_readiness"],
                         "verified_archived_receipts_research_only")
        self.assertEqual(report["eligible_sampled_issuer_years"], 1)
        self.assertEqual(report["provider_xbrl_file_calls"], 1)
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(session.requests[0][1]["rcept_no"], receipt)
        self.assertTrue(report["archived_research_never_promotes_current_day_scores"])
        self.assertTrue(report["no_xbrl_amounts_or_investment_scores_derived"])
        self.assertEqual(untouched, json.dumps(verified, sort_keys=True))
        self.assertEqual(
            pilot._recent_archived_receipt_candidates(
                older, verified, asof=asof, members={}, limit=4), [])

        # The same archive remains usable in dry-run, with no source calls.
        dry = pilot.run_pilot(
            market, catalog, older, verified, mode="dry-run")
        self.assertEqual(dry["provider_xbrl_file_calls"], 0)
        self.assertEqual(dry["eligible_sampled_issuer_years"], 1)

    def test_archived_original_research_rejects_tampering_and_future_checkpoint(self):
        market, catalog, major, current, code, receipt = self.fixture()
        asof, members = cash.asof_members(market, catalog, major)
        older = json.loads(json.dumps(major))
        older["asof"] = (date.fromisoformat(asof) - timedelta(days=1)).isoformat()
        verified = cash.summary(
            older["asof"], members, current["records"], cash.fingerprint(older))
        altered = json.loads(json.dumps(verified))
        altered["records"][code]["years"]["2025"]["annual_receipt_id"] = (
            "20260401000001")
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, older, altered, mode="dry-run")

        future = json.loads(json.dumps(verified))
        future["asof"] = (date.fromisoformat(asof) + timedelta(days=1)).isoformat()
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, older, future, mode="dry-run")

        stale = json.loads(json.dumps(older))
        stale["asof"] = (date.fromisoformat(asof) - timedelta(days=6)).isoformat()
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, stale, verified, mode="dry-run")

    def test_prior_day_cashflow_receipts_are_rechecked_before_any_probe(self):
        market, catalog, major, current, code, receipt = self.fixture()
        asof, members = cash.asof_members(market, catalog, major)
        yesterday = (date.fromisoformat(asof) - timedelta(days=1)).isoformat()
        prior = cash.summary(yesterday, members, current["records"],
                             cash.fingerprint(major))
        report = pilot.run_pilot(market, catalog, major, prior, mode="dry-run")
        self.assertEqual(report["asof"], asof)
        self.assertEqual(report["eligible_sampled_issuer_years"], 1)
        self.assertEqual(report["provider_xbrl_file_calls"], 0)
        live = pilot.run_pilot(
            market, catalog, major, prior, mode="live", key="A" * 40,
            session=Session(archive(sample_xml())))
        self.assertEqual(live["provider_xbrl_file_calls"], 1)
        # Changed, even well-formed, receipt IDs cannot survive rollover.
        changed = json.loads(json.dumps(prior))
        changed["records"][code]["years"]["2025"]["annual_receipt_id"] = (
            "20260401000001")
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, major, changed, mode="dry-run")
        future = cash.summary(
            (date.fromisoformat(asof) + timedelta(days=1)).isoformat(),
            members, current["records"], cash.fingerprint(major))
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.run_pilot(market, catalog, major, future, mode="dry-run")

    def test_missing_annual_krw_reason_priority_and_cross_instance_conflict(self):
        base = (
            '<xbrl xmlns="' + INSTANCE_NS + '" xmlns:ifrs="' + IFRS_NS + '">'
            '<context id="C"><period><startDate>2025-01-01</startDate>'
            '<endDate>2025-12-31</endDate></period></context>'
            '<unit id="U"><measure>iso4217:KRW</measure></unit>'
            '<ifrs:PurchaseOfIntangibleAssets contextRef="C" unitRef="U">'
            '12345</ifrs:PurchaseOfIntangibleAssets></xbrl>'
        ).encode("utf8")
        cases = (
            ("ifrs_tag_missing",
             base.replace(b"PurchaseOfIntangibleAssets", b"OtherAssets")),
            ("annual_context_missing",
             base.replace(b"2025-01-01", b"2024-01-01")),
            ("krw_unit_missing",
             base.replace(b"iso4217:KRW", b"iso4217:USD")),
            ("numeric_fact_invalid",
             base.replace(b">12345<", b">unknown<")),
            ("candidate_scope_unverified", base),
        )
        for reason, xml in cases:
            with self.subTest(reason=reason):
                result = pilot.inspect_binary_archive(archive(xml), year=2025)
                self.assertEqual(
                    result["annual_krw_reasons"]["intangible"], reason)
                self.assertEqual(
                    "intangible" in result["annual_krw_facts"],
                    reason == "candidate_scope_unverified")
        # Two otherwise acceptable values in different ZIP instances cannot
        # be declared unique; statement scope is still unknown either way.
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as writer:
            writer.writestr("one.xbrl", base)
            writer.writestr("two.xbrl", base.replace(b">12345<", b">67890<"))
        conflict = pilot.inspect_binary_archive(output.getvalue(), year=2025)
        self.assertEqual(
            conflict["annual_krw_reasons"]["intangible"],
            "numeric_values_ambiguous")
        self.assertNotIn("intangible", conflict["annual_krw_facts"])

    def test_annual_context_mismatch_is_classified_without_accepting_any_amount(self):
        cases = (
            ("dimensional_target_year",
             "<startDate>2025-01-01</startDate><endDate>2025-12-31</endDate>",
             "<entity><segment><value>synthetic</value></segment></entity>",
             "C"),
            ("alternate_fiscal_twelve_months",
             "<startDate>2024-10-01</startDate><endDate>2025-09-30</endDate>",
             "", "C"),
            ("comparative_annual_period",
             "<startDate>2024-01-01</startDate><endDate>2024-12-31</endDate>",
             "", "C"),
            ("short_current_period",
             "<startDate>2025-01-01</startDate><endDate>2025-09-30</endDate>",
             "", "C"),
            ("other_period",
             "<startDate>2023-01-01</startDate><endDate>2023-12-31</endDate>",
             "", "C"),
            ("unresolved_or_instant",
             "<startDate>2025-01-01</startDate><endDate>2025-12-31</endDate>",
             "", "NOT_FOUND"),
        )
        for expected, period, dimensions, ref in cases:
            with self.subTest(expected=expected):
                xml = (
                    '<xbrl xmlns="' + INSTANCE_NS + '" xmlns:ifrs="' + IFRS_NS + '">'
                    '<context id="C">' + dimensions + '<period>' + period +
                    '</period></context>'
                    '<unit id="U"><measure>iso4217:KRW</measure></unit>'
                    '<ifrs:PurchaseOfIntangibleAssets contextRef="' + ref +
                    '" unitRef="U">123</ifrs:PurchaseOfIntangibleAssets></xbrl>'
                ).encode("utf8")
                result = pilot.inspect_binary_archive(archive(xml), year=2025)
                self.assertEqual(result["annual_krw_reasons"]["intangible"],
                                 "annual_context_missing")
                self.assertEqual(result["annual_context_gap_subreasons"]["intangible"],
                                 expected)
                self.assertNotIn("intangible", result["annual_krw_facts"])
                self.assertNotIn("123", json.dumps(result, default=list))

        current = (
            '<xbrl xmlns="' + INSTANCE_NS + '" xmlns:ifrs="' + IFRS_NS + '">'
            '<context id="C"><period><startDate>2025-01-01</startDate>'
            '<endDate>2025-12-31</endDate></period></context>'
            '<unit id="U"><measure>iso4217:KRW</measure></unit>'
            '<ifrs:PurchaseOfIntangibleAssets contextRef="C" unitRef="U">'
            '123</ifrs:PurchaseOfIntangibleAssets></xbrl>'
        ).encode("utf8")
        result = pilot.inspect_binary_archive(archive(current), year=2025)
        self.assertEqual(result["annual_krw_reasons"]["intangible"],
                         "candidate_scope_unverified")
        self.assertNotIn("intangible", result["annual_context_gap_subreasons"])
        duplicate = current.replace(
            b'<unit id="U">', b'<context id="C"></context><unit id="U">')
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.inspect_binary_archive(archive(duplicate), year=2025)

    def test_local_label_schema_extension_fact_is_discovery_not_score(self):
        schema = (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'targetNamespace="http://company.example/extension">'
            '<xs:element name="AcquireIntangibleCustom" '
            'id="co_AcquireIntangibleCustom" type="xs:integer"/>'
            '</xs:schema>'
        ).encode("utf8")
        link = (
            '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<link:labelLink xlink:type="extended">'
            '<link:loc xlink:type="locator" xlink:label="L" '
            'xlink:href="company.xsd#co_AcquireIntangibleCustom"/>'
            '<link:label xlink:type="resource" xlink:label="R" '
            'xml:lang="ko">무형자산의 취득</link:label>'
            '<link:labelArc xlink:type="arc" '
            'xlink:arcrole="http://www.xbrl.org/2003/arcrole/concept-label" '
            'xlink:from="L" xlink:to="R"/>'
            '</link:labelLink></link:linkbase>'
        ).encode("utf8")
        instance = (
            '<xbrl xmlns="' + INSTANCE_NS + '" '
            'xmlns:co="http://company.example/extension">'
            '<co:AcquireIntangibleCustom contextRef="C" unitRef="U">'
            '123456789</co:AcquireIntangibleCustom></xbrl>'
        ).encode("utf8")

        def source_bundle(label=link, instance_blob=instance):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as writer:
                writer.writestr("company.xsd", schema)
                writer.writestr("labels.xml", label)
                writer.writestr("report.xbrl", instance_blob)
            return output.getvalue()

        observed = pilot.inspect_binary_archive(source_bundle(), year=2025)
        extension = observed["extension_label_evidence"]
        self.assertEqual(extension["local_taxonomy_schema_files"], 1)
        self.assertEqual(extension["local_label_linkbase_files"], 1)
        self.assertEqual(extension["exact_extension_label_schema_leads"], 1)
        self.assertEqual(
            extension["exact_extension_label_instance_facts"]["intangible"], 1)
        self.assertEqual(
            extension["exact_extension_label_instance_facts"]["ppe"], 0)
        self.assertEqual(observed["annual_krw_facts"], set())
        self.assertEqual(extension["extension_annual_krw_status"]["intangible"],
                         "annual_context_missing")
        full_year = (
            '<context id="C"><period><startDate>2025-01-01</startDate>'
            '<endDate>2025-12-31</endDate></period></context>'
            '<unit id="U"><measure>iso4217:KRW</measure></unit>'
        ).encode("utf8")
        valid = instance.replace(b'<co:AcquireIntangibleCustom',
                                 full_year + b'<co:AcquireIntangibleCustom', 1)
        annual = pilot.inspect_binary_archive(
            source_bundle(instance_blob=valid), year=2025)
        self.assertEqual(annual["extension_label_evidence"][
            "extension_annual_krw_status"]["intangible"],
            "candidate_scope_unverified")
        self.assertEqual(annual["annual_krw_facts"], set())
        usd = valid.replace(b"iso4217:KRW", b"iso4217:USD")
        self.assertEqual(pilot.inspect_binary_archive(
            source_bundle(instance_blob=usd), year=2025)[
                "extension_label_evidence"]["extension_annual_krw_status"][
                "intangible"], "krw_unit_missing")
        negative = valid.replace(b">123456789<", b">-123456789<")
        self.assertEqual(pilot.inspect_binary_archive(
            source_bundle(instance_blob=negative), year=2025)[
                "extension_label_evidence"]["extension_annual_krw_status"][
                "intangible"], "numeric_fact_invalid")
        self.assertNotIn("123456789", json.dumps(observed, default=list))
        self.assertNotIn("co_AcquireIntangibleCustom",
                         json.dumps(observed, default=list))

        # Fuzzy labels, remotely hosted schema refs, and wrong concepts do
        # not become leads and are never fetched from external URLs.
        for mismatch in (
            link.replace("무형자산의 취득".encode(), "무형자산의 처분".encode()),
            link.replace(b'company.xsd#', b'https://elsewhere.example/co.xsd#'),
            link.replace(b'co_AcquireIntangibleCustom', b'co_OtherAccount'),
        ):
            with self.subTest(link=mismatch[:60]):
                nope = pilot.inspect_binary_archive(
                    source_bundle(mismatch), year=2025)
                self.assertEqual(
                    nope["extension_label_evidence"][
                        "exact_extension_label_instance_facts"]["intangible"], 0)

        market, catalog, major, state, code, receipt = self.fixture()
        report = pilot.run_pilot(
            market, catalog, major, state, mode="live",
            key="A" * 40, session=Session(source_bundle()))
        self.assertEqual(report["provider_xbrl_file_calls"], 1)
        self.assertEqual(report["reports_with_extension_intangible_label_fact"], 1)
        self.assertEqual(report["extension_candidate_validation_reasons"][
            "annual_context_missing"], 1)
        verified = pilot.run_pilot(
            market, catalog, major, state, mode="live",
            key="A" * 40, session=Session(source_bundle(instance_blob=valid)))
        self.assertEqual(verified[
            "reports_with_extension_intangible_annual_krw_candidate"], 1)
        self.assertTrue(verified[
            "extension_leads_never_certify_cashflow_amount_or_scope"])
        self.assertNotIn("123456789", json.dumps(verified))
        self.assertTrue(
            report["extension_leads_never_certify_cashflow_amount_or_scope"])
        self.assertEqual(report["reports_with_missing_annual_krw_candidate"], 0)
        self.assertNotIn("123456789", json.dumps(report))
        self.assertNotIn(code, json.dumps(report))
        self.assertNotIn(receipt, json.dumps(report))

    def test_live_context_subreason_is_bounded_anonymous_aggregate(self):
        market, catalog, major, state, code, receipt = self.fixture()
        xml = (
            '<xbrl xmlns="' + INSTANCE_NS + '" xmlns:ifrs="' + IFRS_NS + '">'
            '<context id="C"><period><startDate>2024-10-01</startDate>'
            '<endDate>2025-09-30</endDate></period></context>'
            '<unit id="U"><measure>iso4217:KRW</measure></unit>'
            '<ifrs:PurchaseOfIntangibleAssets contextRef="C" unitRef="U">'
            '123456789</ifrs:PurchaseOfIntangibleAssets></xbrl>'
        ).encode("utf8")
        report = pilot.run_pilot(
            market, catalog, major, state, mode="live",
            key="A" * 40, session=Session(archive(xml)))
        self.assertEqual(
            report["missing_source_fact_reasons"]["annual_context_missing"], 1)
        self.assertEqual(
            report["annual_context_gap_subreasons"]["alternate_fiscal_twelve_months"], 1)
        self.assertEqual(sum(report["annual_context_gap_subreasons"].values()), 1)
        self.assertEqual(report["reports_with_missing_annual_krw_candidate"], 0)
        public = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("123456789", public)
        self.assertNotIn(code, public)
        self.assertNotIn(receipt, public)

    def test_rejection_counts_are_missing_fact_totals_only(self):
        market, catalog, major, state, code, receipt = self.fixture()
        # A tag with neither its annual context nor currency evidence.
        result = pilot.run_pilot(
            market, catalog, major, state, mode="live",
            key="A" * 40, session=Session(archive(sample_xml())))
        self.assertEqual(result["checked_missing_source_fact_kinds"], 1)
        self.assertEqual(
            result["missing_source_fact_reasons"]["annual_context_missing"], 1)
        self.assertEqual(
            sum(result["missing_source_fact_reasons"].values()), 1)
        public = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(code, public)
        self.assertNotIn(receipt, public)
        self.assertNotIn("12345", public)

    def test_live_annual_krw_probe_still_never_outputs_amount(self):
        market, catalog, major, state, code, receipt = self.fixture()
        xml = (
            '<xbrl xmlns="' + INSTANCE_NS + '" xmlns:ifrs="' + IFRS_NS + '">'
            '<context id="C"><period><startDate>2025-01-01</startDate>'
            '<endDate>2025-12-31</endDate></period></context>'
            '<unit id="U"><measure>iso4217:KRW</measure></unit>'
            '<ifrs:PurchaseOfIntangibleAssets contextRef="C" unitRef="U">'
            '123456789</ifrs:PurchaseOfIntangibleAssets></xbrl>'
        ).encode("utf8")
        result = pilot.run_pilot(
            market, catalog, major, state, mode="live",
            key="A" * 40, session=Session(archive(xml)))
        self.assertEqual(result["reports_with_missing_annual_krw_candidate"], 1)
        self.assertEqual(
            result["missing_source_fact_reasons"]["candidate_scope_unverified"], 1)
        self.assertEqual(sum(result["missing_source_fact_reasons"].values()), 1)
        public = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("123456789", public)
        self.assertNotIn(code, public)
        self.assertNotIn(receipt, public)


    def test_rotating_cohorts_span_eligible_years_and_issuers_without_leaks(self):
        market, catalog, major, state, code, receipt = self.fixture()
        asof, _ = cash.asof_members(market, catalog, major)
        # Synthetic verified receipts only; never call an outside provider.
        choices_by_class = {
            "missing_both_capex": (100, None, None),
            "missing_intangible": (100, 20, None),
            "missing_ppe": (100, None, 10),
            "missing_cfo": (None, 20, 10),
        }
        records, sources = {}, {}
        for kind_index, (kind, fields) in enumerate(choices_by_class.items()):
            for issuer_index in range(3):
                fake_code = f"{910000 + kind_index * 10 + issuer_index:06d}"
                year = (2025, 2024, 2023)[issuer_index]
                observation = {
                    "major_annual_receipt_matched": True,
                    "annual_receipt_id": receipt,
                    "operating_cashflow_krw": fields[0],
                    "ppe_capex_krw": fields[1],
                    "intangible_capex_krw": fields[2],
                }
                records[fake_code] = {
                    "scope": "CFS", "years": {str(year): observation}}
                sources[fake_code] = {
                    "years": {str(year): {"CFS": {"receipt_id": receipt}}}}
        synthetic_state = {"records": records}
        synthetic_major = {"records": sources}
        cohorts = [
            pilot.select_candidates(
                synthetic_state, synthetic_major, asof=asof, cohort_index=i)
            for i in range(3)
        ]
        for cohort in cohorts:
            self.assertEqual(len(cohort), 4)
            self.assertEqual({item[4] for item in cohort}, set(pilot.CLASSES))
            self.assertEqual(len({item[0] for item in cohort}), 4)
            self.assertEqual({item[1] for item in cohort}, {2023, 2024, 2025})
        self.assertNotEqual(
            [item[0] for item in cohorts[0]],
            [item[0] for item in cohorts[1]])
        self.assertEqual(
            cohorts[0],
            pilot.select_candidates(
                synthetic_state, synthetic_major, asof=asof, cohort_index=0))
        self.assertEqual(len(
            pilot.select_candidates(synthetic_state, synthetic_major,
                                    asof=asof, cohort_index=2, limit=2)), 2)

        # A mismatching receipt is rejected BEFORE rotation/provider calls.
        synthetic_major["records"][cohorts[1][0][0]]["years"][
            str(cohorts[1][0][1])]["CFS"]["receipt_id"] = "20210101000001"
        with self.assertRaises(pilot.XbrlPilotError):
            pilot.select_candidates(
                synthetic_state, synthetic_major, asof=asof, cohort_index=1)

    def test_invalid_cohort_index_rejected_before_any_provider_call(self):
        market, catalog, major, state, _, _ = self.fixture()
        for invalid in (-1, 1000001, True, "1", 1.5):
            with self.subTest(value=invalid):
                with self.assertRaises(pilot.XbrlPilotError):
                    pilot.run_pilot(
                        market, catalog, major, state,
                        mode="live", key="A" * 40,
                        session=Session(archive(sample_xml())),
                        cohort_index=invalid)
        dry = pilot.run_pilot(
            market, catalog, major, state, mode="dry-run", cohort_index=25)
        self.assertEqual(dry["sample_cohort_index"], 25)
        self.assertEqual(dry["sample_cohort_distinct_issuers"], 1)
        self.assertEqual(dry["provider_xbrl_file_calls"], 0)
        self.assertTrue(dry["no_xbrl_amounts_or_investment_scores_derived"])


    def test_extension_context_gap_reports_safe_specific_shapes(self):
        schema = (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'targetNamespace="http://synthetic.example/extension">'
            '<xs:element name="PpeCustom" id="co_PpeCustom" '
            'type="xs:integer"/></xs:schema>'
        ).encode("utf8")
        labels = (
            '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<link:labelLink xlink:type="extended">'
            '<link:loc xlink:label="L" xlink:href="company.xsd#co_PpeCustom"/>'
            '<link:label xlink:label="R" xml:lang="ko">유형자산 취득</link:label>'
            '<link:labelArc xlink:arcrole='
            '"http://www.xbrl.org/2003/arcrole/concept-label" '
            'xlink:from="L" xlink:to="R"/>'
            '</link:labelLink></link:linkbase>'
        ).encode("utf8")
        def packaged(context):
            instance = (
                '<xbrl xmlns="' + INSTANCE_NS + '" '
                'xmlns:co="http://synthetic.example/extension">'
                '<context id="C">' + context + '</context>'
                '<unit id="U"><measure>iso4217:KRW</measure></unit>'
                '<co:PpeCustom contextRef="C" unitRef="U">'
                '987654321</co:PpeCustom></xbrl>'
            ).encode("utf8")
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as dst:
                dst.writestr("company.xsd", schema)
                dst.writestr("label.xml", labels)
                dst.writestr("instance.xbrl", instance)
            return output.getvalue()
        cases = [
            ("dimensional_target_year",
             '<entity><segment><other>synthetic</other></segment></entity>'
             '<period><startDate>2025-01-01</startDate>'
             '<endDate>2025-12-31</endDate></period>'),
            ("alternate_fiscal_twelve_months",
             '<period><startDate>2024-10-01</startDate>'
             '<endDate>2025-09-30</endDate></period>'),
            ("comparative_annual_period",
             '<period><startDate>2024-01-01</startDate>'
             '<endDate>2024-12-31</endDate></period>'),
        ]
        for expected, context in cases:
            with self.subTest(expected=expected):
                bundle = packaged(context)
                observation = pilot.inspect_binary_archive(bundle, year=2025)
                self.assertEqual(observation["extension_label_evidence"][
                    "extension_annual_context_gap_subreasons"]["ppe"], expected)
                market, catalog, major, state, code, receipt = self.fixture()
                # This fixture misses intangible, but XBRL extension evidence
                # is still observational, independent from a score.
                report = pilot.run_pilot(
                    market, catalog, major, state, mode="live",
                    key="A" * 40, session=Session(bundle))
                self.assertEqual(report["extension_annual_context_gap_subreasons"][
                    expected], 1)
                self.assertEqual(sum(report[
                    "extension_annual_context_gap_subreasons"].values()), 1)
                self.assertEqual(report[
                    "reports_with_extension_ppe_annual_krw_candidate"], 0)
                public = json.dumps(report, ensure_ascii=False)
                for sensitive in ("987654321", code, receipt, "co_PpeCustom"):
                    self.assertNotIn(sensitive, public)
        # Correct exact-year nondimensional duration is a *candidate* only,
        # never a missing annual context or certified score.
        exact = packaged(
            '<period><startDate>2025-01-01</startDate>'
            '<endDate>2025-12-31</endDate></period>')
        good = pilot.inspect_binary_archive(exact, year=2025)
        self.assertEqual(good["extension_label_evidence"][
            "extension_annual_krw_status"]["ppe"], "candidate_scope_unverified")
        self.assertEqual(good["extension_label_evidence"][
            "extension_annual_context_gap_subreasons"], {})

    def test_cohort_fiscal_year_summary_remains_anonymous_and_bounded(self):
        market, catalog, major, state, code, receipt = self.fixture()
        report = pilot.run_pilot(
            market, catalog, major, state, mode="dry-run", cohort_index=202)
        self.assertEqual(sum(report["sample_fiscal_year_counts"].values()), 1)
        self.assertEqual(set(report["sample_fiscal_year_counts"]),
                         {str(year) for year in cash.YEARS})
        self.assertEqual(report["sample_cohort_index"], 202)
        self.assertEqual(report["provider_xbrl_file_calls"], 0)
        self.assertNotIn(code, json.dumps(report))
        self.assertNotIn(receipt, json.dumps(report))


    def test_extension_dimensional_shape_is_only_structural_evidence(self):
        schema = (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'targetNamespace="http://synthetic.example/corp">'
            '<xs:element name="PpeAdditions" id="co_PpeAdditions" '
            'type="xs:integer"/></xs:schema>'
        ).encode("utf8")
        labels = (
            '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<link:labelLink><link:loc xlink:label="L" '
            'xlink:href="corp.xsd#co_PpeAdditions"/>'
            '<link:label xlink:label="R" xml:lang="ko">'
            '유형자산의 취득</link:label>'
            '<link:labelArc xlink:arcrole='
            '"http://www.xbrl.org/2003/arcrole/concept-label" '
            'xlink:from="L" xlink:to="R"/>'
            '</link:labelLink></link:linkbase>'
        ).encode("utf8")
        def zip_with(segment, extra_fact=""):
            instance = (
                '<xbrl xmlns="' + INSTANCE_NS + '" '
                'xmlns:di="http://xbrl.org/2006/xbrldi" '
                'xmlns:co="http://synthetic.example/corp" '
                'xmlns:ifrs="' + IFRS_NS + '" '
                'xmlns:d="http://synthetic.example/dim">'
                '<context id="C"><entity><segment>' + segment +
                '</segment></entity><period>'
                '<startDate>2025-01-01</startDate>'
                '<endDate>2025-12-31</endDate></period></context>'
                '<unit id="U"><measure>iso4217:KRW</measure></unit>'
                '<co:PpeAdditions contextRef="C" unitRef="U">'
                '123456789</co:PpeAdditions>' + extra_fact + '</xbrl>'
            ).encode("utf8")
            b = io.BytesIO()
            with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as f:
                f.writestr("corp.xsd", schema)
                f.writestr("labels.xml", labels)
                f.writestr("report.xbrl", instance)
            return b.getvalue()
        explicit = ('<di:explicitMember dimension="d:Axis">'
                    'd:ScopeMember</di:explicitMember>')
        cases = (
            ("single_explicit_member", explicit),
            ("multiple_explicit_members", explicit + explicit),
            ("typed_member_present",
             '<di:typedMember dimension="d:Axis"><d:Name>'
             'dummy</d:Name></di:typedMember>'),
            ("nonstandard_segment_content", '<other>dummy</other>'),
        )
        for shape, segment in cases:
            with self.subTest(shape=shape):
                body = zip_with(segment)
                result = pilot.inspect_binary_archive(body, year=2025)
                extension = result["extension_label_evidence"]
                self.assertEqual(extension[
                    "extension_target_year_dimension_shapes"]["ppe"], shape)
                self.assertEqual(extension[
                    "extension_annual_context_gap_subreasons"]["ppe"],
                    "dimensional_target_year")
                market, catalog, major, state, code, receipt = self.fixture()
                report = pilot.run_pilot(
                    market, catalog, major, state, mode="live",
                    key="A" * 40, session=Session(body))
                self.assertEqual(report[
                    "extension_target_year_dimension_shapes"][shape], 1)
                self.assertEqual(sum(report[
                    "extension_target_year_dimension_shapes"].values()), 1)
                expected_hint = ("other_axis_member" if
                                 shape == "single_explicit_member" else
                                 "not_single_explicit_member")
                self.assertEqual(report[
                    "extension_target_year_ifrs_scope_hints"][expected_hint], 1)
                self.assertEqual(sum(report[
                    "extension_target_year_ifrs_scope_hints"].values()), 1)
                self.assertEqual(report[
                    "reports_with_extension_ppe_annual_krw_candidate"], 0)
                self.assertTrue(report["no_statement_scope_verified"])
                exposed = json.dumps(report, ensure_ascii=False)
                for private in ("123456789", code, receipt, "PpeAdditions",
                                "ScopeMember"):
                    self.assertNotIn(private, exposed)


        # Same contextRef is evidence of co-location only; it cannot prove
        # primary cash-flow statement membership or CFS/OFS consolidation.
        cfo = ('<ifrs:CashFlowsFromUsedInOperatingActivities '
               'contextRef="C" unitRef="U">7654321'
               '</ifrs:CashFlowsFromUsedInOperatingActivities>')
        matching = zip_with(explicit, cfo)
        matched = pilot.inspect_binary_archive(matching, year=2025)
        evidence = matched["extension_label_evidence"]
        self.assertTrue(evidence[
            "extension_target_year_shared_ifrs_cfo_contexts"]["ppe"])
        market, catalog, major, state, code, receipt = self.fixture()
        shared_report = pilot.run_pilot(
            market, catalog, major, state, mode="live", key="A" * 40,
            session=Session(matching))
        self.assertEqual(shared_report[
            "extension_target_year_ifrs_cfo_context_overlap"]["shared"], 1)
        self.assertEqual(shared_report[
            "extension_target_year_ifrs_cfo_context_overlap"]["not_shared"], 0)
        self.assertTrue(shared_report["no_statement_scope_verified"])
        self.assertEqual(shared_report[
            "reports_with_extension_ppe_annual_krw_candidate"], 0)
        # Another context ID—even in the same instance—is not a match.
        mismatch = zip_with(explicit, cfo.replace(
            'contextRef="C"', 'contextRef="OTHER"'))
        no_shared = pilot.inspect_binary_archive(mismatch, year=2025)
        self.assertFalse(no_shared["extension_label_evidence"][
            "extension_target_year_shared_ifrs_cfo_contexts"]["ppe"])
        mismatch_report = pilot.run_pilot(
            market, catalog, major, state, mode="live", key="A" * 40,
            session=Session(mismatch))
        self.assertEqual(mismatch_report[
            "extension_target_year_ifrs_cfo_context_overlap"]["not_shared"], 1)
        public = json.dumps(shared_report) + json.dumps(mismatch_report)
        for sensitive in ("123456789", "7654321", code, receipt,
                          "PpeAdditions", "ScopeMember"):
            self.assertNotIn(sensitive, public)


    def test_ifrs_scope_axis_qname_hints_fail_closed_and_never_certify(self):
        # IFRS scope axis/member is a hint, not the original taxonomy DTS
        # domain validation or cash-flow-statement role evidence.
        axis = "ConsolidatedAndSeparateFinancialStatementsAxis"
        prefix = "ifrs"
        member = "ConsolidatedMember"
        dim = "http://xbrl.org/2006/xbrldi"
        instance = (
            '<xbrl xmlns="' + INSTANCE_NS + '" '
            'xmlns:di="' + dim + '" '
            'xmlns:ifrs="' + IFRS_NS + '" '
            'xmlns:co="http://synthetic.example/issuer">'
            '<context id="C"><entity><segment>'
            '<di:explicitMember dimension="' + prefix + ':' + axis + '">'
            + prefix + ':' + member + '</di:explicitMember>'
            '</segment></entity><period>'
            '<startDate>2025-01-01</startDate><endDate>2025-12-31'
            '</endDate></period></context>'
            '<unit id="U"><measure>iso4217:KRW</measure></unit>'
            '<co:CapitalSpending contextRef="C" unitRef="U">'
            '2468013579</co:CapitalSpending></xbrl>'
        ).encode("utf8")
        matched = {
            "ppe": {"{http://synthetic.example/issuer}CapitalSpending"},
            "intangible": set(),
        }
        def hint(data):
            result = pilot._extension_target_year_ifrs_scope_hints(
                data, 2025, matched)
            self.assertEqual(result["intangible"], set())
            self.assertNotIn("2468013579", json.dumps(
                {kind: sorted(values) for kind, values in result.items()}))
            return result["ppe"]
        self.assertEqual(hint(instance),
                         {"ifrs_consolidated_member_hint"})
        self.assertEqual(hint(instance.replace(
            b"ConsolidatedMember", b"SeparateMember")),
            {"ifrs_separate_member_hint"})
        self.assertEqual(hint(instance.replace(
            b"ConsolidatedMember", b"OtherMember")),
            {"ifrs_scope_axis_other_member"})
        self.assertEqual(hint(instance.replace(
            b"ConsolidatedAndSeparateFinancialStatementsAxis",
            b"OtherAxis")),
            {"other_axis_member"})
        self.assertEqual(hint(instance.replace(
            b"ifrs:ConsolidatedMember", b"missing:ConsolidatedMember")),
            {"unresolved_qname"})
        # A rebinding of the SAME prefix elsewhere must not be guessed
        # from a document-global prefix map.
        rebound = instance.replace(
            b'<unit id="U">',
            b'<unused xmlns:ifrs="urn:other"></unused><unit id="U">')
        self.assertEqual(hint(rebound), {"unresolved_qname"})
        # A date mismatch cannot reuse an apparently familiar axis.
        self.assertEqual(hint(instance.replace(
            b"2025-12-31", b"2024-12-31")), set())
        # Multiple explicit members cannot produce a scope hint.
        doubled = instance.replace(
            b'</di:explicitMember>', b'</di:explicitMember>'
            b'<di:explicitMember dimension="ifrs:OtherAxis">'
            b'ifrs:ConsolidatedMember</di:explicitMember>')
        self.assertEqual(hint(doubled), {"not_single_explicit_member"})


    def test_zip_local_presentation_hierarchy_is_only_a_structural_hint(self):
        schema = (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'targetNamespace="http://company.example/extension">'
            '<xs:element name="CustomPPE" id="co_CustomPPE" type="xs:integer"/>'
            '<xs:element name="CashflowParent" id="co_CashflowParent" '
            'type="xs:integer"/>'
            '</xs:schema>'
        ).encode("utf8")
        labels = (
            '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<link:labelLink xlink:type="extended">'
            '<link:loc xlink:label="P" '
            'xlink:href="company.xsd#co_CustomPPE"/>'
            '<link:label xlink:label="R" xml:lang="ko">유형자산의 취득'
            '</link:label><link:labelArc xlink:from="P" xlink:to="R" '
            'xlink:arcrole="http://www.xbrl.org/2003/arcrole/concept-label"/>'
            '</link:labelLink></link:linkbase>'
        ).encode("utf8")
        instance = (
            '<xbrl xmlns="' + INSTANCE_NS + '" '
            'xmlns:co="http://company.example/extension">'
            '<context id="C"><period><startDate>2025-01-01</startDate>'
            '<endDate>2025-12-31</endDate></period></context>'
            '<unit id="U"><measure>iso4217:KRW</measure></unit>'
            '<co:CustomPPE contextRef="C" unitRef="U">'
            '987654321</co:CustomPPE></xbrl>'
        ).encode("utf8")

        def presentation(direction, *, extra_role=False, external=False):
            link_start = (
                '<link:presentationLink xlink:type="extended" '
                'xlink:role="http://example.test/role/CF">'
                '<link:loc xlink:label="P" '
                'xlink:href="' +
                ('https://external.test/concept.xsd#co_CustomPPE'
                 if external else 'company.xsd#co_CustomPPE') + '"/>'
                '<link:loc xlink:label="Q" '
                'xlink:href="company.xsd#co_CashflowParent"/>')
            arcs = {
                "child": '<link:presentationArc xlink:from="Q" xlink:to="P" ',
                "parent": '<link:presentationArc xlink:from="P" xlink:to="Q" ',
                "locator": '',
            }
            relation = arcs[direction]
            if relation:
                relation += ('xlink:arcrole='
                             '"http://www.xbrl.org/2003/arcrole/parent-child"/>')
            first = link_start + relation + '</link:presentationLink>'
            second = (
                '<link:presentationLink xlink:type="extended" '
                'xlink:role="http://example.test/role/notes">'
                '<link:loc xlink:label="R" '
                'xlink:href="company.xsd#co_CustomPPE"/>'
                '</link:presentationLink>'
                if extra_role else '')
            return (
                '<link:linkbase '
                'xmlns:link="http://www.xbrl.org/2003/linkbase" '
                'xmlns:xlink="http://www.w3.org/1999/xlink">' +
                first + second + '</link:linkbase>').encode("utf8")

        def bundle(presentation_blob=None):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as writer:
                writer.writestr("company.xsd", schema)
                writer.writestr("labels.xml", labels)
                writer.writestr("report.xbrl", instance)
                if presentation_blob is not None:
                    writer.writestr("presentation.xml", presentation_blob)
            return output.getvalue()

        cases = (
            (None, "no_local_presentation_link"),
            (presentation("child"), "child_only_in_local_role"),
            (presentation("parent"), "parent_only_in_local_role"),
            (presentation("locator"), "locator_only_no_parent_child_arc"),
            (presentation("child", extra_role=True), "multiple_roles_unverified"),
            (presentation("child", external=True),
             "concept_not_in_local_presentation"),
        )
        for presentation_blob, expected in cases:
            with self.subTest(expected=expected):
                result = pilot.inspect_binary_archive(
                    bundle(presentation_blob), year=2025)
                extension = result["extension_label_evidence"]
                self.assertEqual(
                    extension["extension_local_presentation_shapes"]["ppe"],
                    expected)
                self.assertEqual(
                    extension["exact_extension_label_instance_facts"]["ppe"], 1)
                self.assertNotIn("987654321", json.dumps(extension))
                self.assertNotIn("http://example.test/role/CF",
                                 json.dumps(extension))
                self.assertNotIn("CustomPPE", json.dumps(extension))
        market, catalog, major, state, code, receipt = self.fixture()
        report = pilot.run_pilot(
            market, catalog, major, state, mode="live", key="A" * 40,
            session=Session(bundle(presentation("child"))))
        self.assertEqual(report["provider_xbrl_file_calls"], 1)
        self.assertEqual(
            report["extension_local_presentation_shapes"][
                "child_only_in_local_role"], 1)
        self.assertTrue(
            report["presentation_hierarchy_never_certifies_total_or_scope"])
        self.assertTrue(report["no_xbrl_amounts_or_investment_scores_derived"])
        public = json.dumps(report)
        for secret in (code, receipt, "987654321", "CustomPPE"):
            self.assertNotIn(secret, public)


if __name__ == "__main__":
    unittest.main()
