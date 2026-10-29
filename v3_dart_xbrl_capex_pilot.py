"""Read-only, receipt-pinned OpenDART XBRL archive structural evidence pilot.

This does NOT recover a CAPEX amount, financial score, PIT evidence or XBRL
statement scope. Four bounded official file calls at most. Only aggregate
booleans/counts are printed; source archives and issuer identities are never
written to disk or public workflow logs.
"""
from __future__ import annotations

import argparse
from datetime import date
import io
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from v3_dart_bulk_history import _inputs, _day, _valid_receipt
from v3_dart_cashflow_enrichment import (YEARS, verify_state, summary, fingerprint,
                                         retained_current_market_cashflow)

MAX_PROBES = 4
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 14 * 1024 * 1024
MAX_ARCHIVE_BYTES = 44 * 1024 * 1024
MAX_MEMBERS = 128
MAX_RATIO = 250
XBRL_INSTANCE_NS = "http://www.xbrl.org/2003/instance"
CLASSES = ("missing_both_capex", "missing_intangible", "missing_ppe", "missing_cfo")
FACT_NAMES = {
    "cfo": frozenset((
        "CashFlowsFromUsedInOperatingActivities",
    )),
    "ppe": frozenset((
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PurchaseOfPropertyPlantAndEquipment",
    )),
    "intangible": frozenset((
        "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
        "PurchaseOfIntangibleAssets",
    )),
}


class XbrlPilotError(ValueError):
    """Fixed error identifiers only, with no sensitive provider payloads."""


def _classify(record):
    cfo = record["operating_cashflow_krw"]
    ppe = record["ppe_capex_krw"]
    intangible = record["intangible_capex_krw"]
    if cfo is None:
        return "missing_cfo"
    if ppe is None and intangible is None:
        return "missing_both_capex"
    if ppe is None:
        return "missing_ppe"
    if intangible is None:
        return "missing_intangible"
    return None


def select_candidates(state, major, *, asof, limit=MAX_PROBES):
    """One issuer per missingness class, with newest verified receipt first."""
    if type(limit) is not int or not 1 <= limit <= MAX_PROBES:
        raise XbrlPilotError("invalid_bounded_xbrl_limit")
    choices = {kind: [] for kind in CLASSES}
    for code, issuer in state["records"].items():
        scope = issuer["scope"]
        for year in reversed(YEARS):
            found = issuer["years"].get(str(year))
            if not found or found.get("major_annual_receipt_matched") is not True:
                continue
            kind = _classify(found)
            if kind is None:
                continue
            receipt = found["annual_receipt_id"]
            src = major["records"].get(code, {}).get("years", {}).get(
                str(year), {}).get(scope, {})
            if (src.get("receipt_id") != receipt
                or not _valid_receipt(receipt, year, _day(asof))):
                raise XbrlPilotError("xbrl_receipt_identity_changed")
            choices[kind].append((code, year, scope, receipt, kind))
            break
    used = set()
    selected = []
    for kind in CLASSES:
        for choice in sorted(choices[kind], key=lambda x: (-x[1], x[0])):
            if choice[0] not in used:
                selected.append(choice)
                used.add(choice[0])
                break
        if len(selected) == limit:
            break
    return selected


def _recent_archived_receipt_candidates(major, state, *, asof, members, limit):
    """Allow bounded ORIGINAL-FILE research without pretending same-day scoring.

    This is deliberately narrower than market/financial-score rebaselining:
    independently check a complete cashflow checkpoint, its exact source hash,
    filing receipts as of the archive's own date, and current ticker membership.
    A mismatched archive/checkpoint means no calls, never an inferred amount.
    """
    archive_day = _day(major.get("asof"))
    current_day = _day(asof)
    if (archive_day >= current_day or archive_day.year != current_day.year
        or (current_day - archive_day).days > 5):
        raise XbrlPilotError("unsafe_original_archive_age")
    if type(state) is not dict:
        raise XbrlPilotError("untrusted_original_archive_checkpoint")
    checkpoint_day = _day(state.get("asof"))
    if not (archive_day <= checkpoint_day <= current_day):
        raise XbrlPilotError("unsafe_original_archive_checkpoint_day")
    if (type(major.get("records")) is not dict
        or type(major.get("listed_total")) is not int
        or not 1 <= major["listed_total"] <= 6000
        or len(major["records"]) > major["listed_total"]
        or major.get("fiscal_years") != list(range(archive_day.year - 4, archive_day.year))):
        raise XbrlPilotError("untrusted_original_annual_archive")
    source_hash = fingerprint(major)
    if state.get("source_major_history_sha256") != source_hash:
        return None
    records = state.get("records")
    listed = state.get("listed_total")
    if (type(records) is not dict or type(listed) is not int
        or not 1 <= listed <= 6000 or len(records) > listed):
        raise XbrlPilotError("untrusted_original_archive_checkpoint")
    # summary validates every stored CFO/CAPEX formula, scope and provenance,
    # without rewriting the saved as-of or inventing a historical market list.
    reconstructed = summary(state["asof"], records, records, source_hash)
    reconstructed["listed_total"] = listed
    if reconstructed != state:
        raise XbrlPilotError("tampered_original_archive_checkpoint")
    eligible = dict(state)
    eligible["records"] = {code: row for code, row in records.items()
                           if code in members}
    return select_candidates(eligible, major, asof=major["asof"], limit=limit)


def _qualified_name(tag):
    if type(tag) is not str or not tag.startswith("{") or "}" not in tag:
        return "", ""
    namespace, local = tag[1:].split("}", 1)
    return namespace, local


def _ifrs_facts(xml_data):
    """Read tag presence only; never read or use fact amounts."""
    if b"<!DOCTYPE" in xml_data.upper() or b"<!ENTITY" in xml_data.upper():
        raise XbrlPilotError("unsafe_xbrl_xml_entities")
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        raise XbrlPilotError("unparseable_xbrl_instance") from None
    if root.tag != "{" + XBRL_INSTANCE_NS + "}xbrl":
        return None
    found = set()
    for element in root:
        namespace, local = _qualified_name(element.tag)
        if not namespace.startswith((
            "http://xbrl.ifrs.org/taxonomy/",
            "https://xbrl.ifrs.org/taxonomy/",
            "http://www.ifrs.org/",
            "https://www.ifrs.org/",
        )):
            continue
        if "contextRef" not in element.attrib or "unitRef" not in element.attrib:
            continue
        for kind, names in FACT_NAMES.items():
            if local in names:
                found.add(kind)
    return found



def _annual_krw_evidence(xml_data, year):
    """Classify missing annual KRW evidence without exposing source amounts."""
    if year not in YEARS:
        raise XbrlPilotError("unsupported_xbrl_fiscal_year")
    if b"<!DOCTYPE" in xml_data.upper() or b"<!ENTITY" in xml_data.upper():
        raise XbrlPilotError("unsafe_xbrl_xml_entities")
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        raise XbrlPilotError("unparseable_xbrl_instance") from None
    evidence = {
        kind: {"tag": False, "annual": False, "krw": False,
               "numeric": False, "values": set(), "context_shapes": set()}
        for kind in FACT_NAMES
    }
    if root.tag != "{" + XBRL_INSTANCE_NS + "}xbrl":
        return evidence
    ns = "{" + XBRL_INSTANCE_NS + "}"
    units = set()
    for unit in root.findall(ns + "unit"):
        measures = unit.findall(ns + "measure")
        if (len(measures) == 1 and measures[0].text is not None
            and measures[0].text.strip() == "iso4217:KRW"
            and type(unit.get("id")) is str):
            units.add(unit.get("id"))
    # Diagnose *why* a fact missed the exact annual calendar context.
    # Never use a near-year, fiscal-offset or dimensional context to accept
    # a candidate amount; these are anonymous diagnostics only.
    context_shapes = {}
    for context in root.findall(ns + "context"):
        ident = context.get("id")
        if type(ident) is not str or not ident:
            continue
        if ident in context_shapes:
            raise XbrlPilotError("duplicate_xbrl_context_reference")
        periods = context.findall(ns + "period")
        start = end = None
        if len(periods) == 1:
            start = periods[0].findtext(ns + "startDate")
            end = periods[0].findtext(ns + "endDate")
        dimensional = (
            context.find(ns + "segment") is not None
            or context.find(ns + "scenario") is not None
            or context.find(ns + "entity/" + ns + "segment") is not None)
        if start == f"{year}-01-01" and end == f"{year}-12-31":
            shape = ("dimensional_target_year" if dimensional
                     else "exact_calendar_annual")
        else:
            try:
                first, last = date.fromisoformat(start), date.fromisoformat(end)
                span = (last - first).days + 1
            except (ValueError, TypeError):
                first = last = None
                span = 0
            if first is not None and last is not None and span > 0:
                if (last.year == year and 364 <= span <= 366):
                    shape = "alternate_fiscal_twelve_months"
                elif last.year == year - 1 and 364 <= span <= 366:
                    shape = "comparative_annual_period"
                elif last.year == year and span < 364:
                    shape = "short_current_period"
                else:
                    shape = "other_period"
            else:
                shape = "unresolved_or_instant"
        context_shapes[ident] = shape
    for element in root:
        namespace, local = _qualified_name(element.tag)
        if not namespace.startswith((
            "http://xbrl.ifrs.org/taxonomy/",
            "https://xbrl.ifrs.org/taxonomy/",
            "http://www.ifrs.org/",
            "https://www.ifrs.org/",
        )):
            continue
        if "contextRef" not in element.attrib or "unitRef" not in element.attrib:
            continue
        for kind, names in FACT_NAMES.items():
            if local not in names:
                continue
            found = evidence[kind]
            found["tag"] = True
            shape = context_shapes.get(
                element.get("contextRef"), "unresolved_or_instant")
            if shape != "exact_calendar_annual":
                found["context_shapes"].add(shape)
                continue
            found["annual"] = True
            if element.get("unitRef") not in units:
                continue
            found["krw"] = True
            if element.get("{http://www.w3.org/2001/XMLSchema-instance}nil") in ("true", "1"):
                continue
            raw = (element.text or "").strip()
            if not re.fullmatch(r"-?[0-9]{1,18}", raw):
                continue
            value = int(raw)
            if kind != "cfo" and value < 0:
                continue
            found["numeric"] = True
            found["values"].add(value)
    return evidence


CONTEXT_GAP_PRIORITY = (
    "dimensional_target_year", "alternate_fiscal_twelve_months",
    "comparative_annual_period", "short_current_period",
    "other_period", "unresolved_or_instant",
)


def _context_gap_reason(evidence):
    """Only explain a missing annual context; never upgrade a fact."""
    return next((shape for shape in CONTEXT_GAP_PRIORITY
                 if shape in evidence["context_shapes"]),
                "unresolved_or_instant")


def _annual_krw_reasons(evidence):
    """One mutually exclusive outcome per IFRS fact kind across the ZIP."""
    reasons = {}
    for kind, state in evidence.items():
        if not state["tag"]:
            reason = "ifrs_tag_missing"
        elif not state["annual"]:
            reason = "annual_context_missing"
        elif not state["krw"]:
            reason = "krw_unit_missing"
        elif not state["numeric"]:
            reason = "numeric_fact_invalid"
        elif len(state["values"]) != 1:
            reason = "numeric_values_ambiguous"
        else:
            reason = "candidate_scope_unverified"
        reasons[kind] = reason
    return reasons


def inspect_binary_archive(content, *, year=None):
    """No extraction to filesystem; hard limits before decompression."""
    if type(content) is not bytes or not content or len(content) > MAX_RESPONSE_BYTES:
        raise XbrlPilotError("xbrl_binary_response_out_of_bounds")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        # OpenDART sometimes returns an XML API status instead of a ZIP.
        if len(content) < 8192 and b"<!DOCTYPE" not in content.upper():
            try:
                root = ET.fromstring(content)
                status = root.findtext("status")
                if status in ("013", "014"):
                    return {"file_absent": True, "instances": 0, "facts": set(),
                            "annual_krw_facts": set(), "annual_krw_reasons": {},
                            "annual_context_gap_subreasons": {}}
            except ET.ParseError:
                pass
        raise XbrlPilotError("unexpected_xbrl_non_zip_provider_response")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_MEMBERS:
                raise XbrlPilotError("xbrl_zip_member_count_exceeded")
            total = 0
            for entry in entries:
                name = entry.filename
                if (name.startswith("/") or "\\" in name
                    or ".." in name.split("/") or entry.flag_bits & 1):
                    raise XbrlPilotError("unsafe_xbrl_archive_member")
                total += entry.file_size
                if (entry.file_size > MAX_MEMBER_BYTES
                    or total > MAX_ARCHIVE_BYTES
                    or (entry.file_size > MAX_RATIO * max(1, entry.compress_size))):
                    raise XbrlPilotError("xbrl_zip_decompression_bounds_exceeded")
            instances = 0
            facts = set()
            annual = {
                kind: {"tag": False, "annual": False, "krw": False,
                       "numeric": False, "values": set(), "context_shapes": set()}
                for kind in FACT_NAMES
            }
            for entry in entries:
                if entry.is_dir() or not entry.filename.lower().endswith(
                    (".xml", ".xbrl")):
                    continue
                # Never persist archive entries and never disclose filenames.
                data = archive.read(entry)
                identified = _ifrs_facts(data)
                if identified is None:
                    continue
                instances += 1
                facts.update(identified)
                if year is not None:
                    observed = _annual_krw_evidence(data, year)
                    for kind in FACT_NAMES:
                        for stage in ("tag", "annual", "krw", "numeric"):
                            annual[kind][stage] |= observed[kind][stage]
                        annual[kind]["values"].update(observed[kind]["values"])
                        annual[kind]["context_shapes"].update(
                            observed[kind]["context_shapes"])
            reasons = _annual_krw_reasons(annual) if year is not None else {}
            candidates = {kind for kind, reason in reasons.items()
                          if reason == "candidate_scope_unverified"}
            return {"file_absent": False, "instances": instances, "facts": facts,
                    "annual_krw_facts": candidates, "annual_krw_reasons": reasons,
                    "annual_context_gap_subreasons": {
                        kind: _context_gap_reason(annual[kind])
                        for kind, reason in reasons.items()
                        if reason == "annual_context_missing"}}
    except (zipfile.BadZipFile, OSError):
        raise XbrlPilotError("xbrl_zip_unreadable") from None


def _read_response_limited(response):
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=65536):
        if type(chunk) is not bytes:
            raise XbrlPilotError("invalid_xbrl_response_chunk")
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise XbrlPilotError("xbrl_response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def run_pilot(market, catalog, major, state, *, mode, key="", session=None,
              limit=MAX_PROBES):
    try:
        if mode not in ("dry-run", "live") or type(limit) is not int or not 1 <= limit <= MAX_PROBES:
            raise XbrlPilotError("invalid_bounded_xbrl_limit_or_mode")
        asof, members = _inputs(market, catalog)
        if type(major) is not dict:
            raise XbrlPilotError("saved_xbrl_source_identity_invalid")
        # The annual DART archive can legitimately lag today's market scan.
        # An explicitly recent older snapshot is deferred, never combined with
        # current-market issuer identities and never sent to the provider.
        older = major.get("asof")
        source_pending = (type(older) is str and
                          0 < (_day(asof) - _day(older)).days <= 5)
        archived_research = False
        if source_pending:
            # An archived annual source is usable ONLY for read-only original
            # receipt research if an independently verified checkpoint still
            # refers to the exact byte-for-byte annual archive. No scores or
            # current-day financial observations are constructed here.
            choices = _recent_archived_receipt_candidates(
                major, state, asof=asof, members=members, limit=limit)
            archived_research = choices is not None
            if choices is None:
                choices = []
        else:
            # Revalidate prior-day observations against the current issuer set
            # and unchanged major filing receipts, with zero provider requests.
            state = retained_current_market_cashflow(market, catalog, major, state)
            verify_state(state, market, catalog, major)
            choices = select_candidates(state, major, asof=asof, limit=limit)
    except (ValueError, TypeError, KeyError):
        raise XbrlPilotError("saved_xbrl_source_identity_invalid") from None
    report = {
        "schema": "v3-xbrl-archive-capex-structure-pilot-0.4",
        "asof": asof,
        "mode": mode,
        "eligible_sampled_issuer_years": len(choices),
        "source_readiness": ("verified_archived_receipts_research_only"
                             if archived_research else
                             "awaiting_same_day_annual_archive"
                             if source_pending else "receipt_validated"),
        "provider_xbrl_file_calls": 0,
        "valid_zip_files": 0,
        "no_original_file": 0,
        "xbrl_instances_detected": 0,
        "reports_with_ifrs_cfo_tag_candidate": 0,
        "reports_with_ifrs_ppe_tag_candidate": 0,
        "reports_with_ifrs_intangible_tag_candidate": 0,
        "reports_with_any_missing_source_tag": 0,
        "reports_with_missing_annual_krw_candidate": 0,
        "checked_missing_source_fact_kinds": 0,
        "missing_source_fact_reasons": {
            reason: 0 for reason in (
                "original_file_absent", "ifrs_tag_missing",
                "annual_context_missing", "krw_unit_missing",
                "numeric_fact_invalid", "numeric_values_ambiguous",
                "candidate_scope_unverified",
            )
        },
        "annual_context_gap_subreasons": {
            shape: 0 for shape in CONTEXT_GAP_PRIORITY
        },
        "missing_not_interpreted_as_zero": True,
        "archived_research_never_promotes_current_day_scores": True,
        "no_statement_scope_verified": True,
        "annual_krw_facts_not_scored": True,
        "no_xbrl_amounts_or_investment_scores_derived": True,
        "no_issuer_or_receipt_or_raw_xbrl_published": True,
        "note": "Annual KRW candidate tags only; financial statement scope unverified.",
    }
    if mode == "dry-run" or (source_pending and not archived_research):
        return report
    if (mode != "live" or type(key) is not str or len(key) != 40
        or session is None):
        raise XbrlPilotError("live_xbrl_key_or_session_unavailable")
    for code, year, scope, receipt, kind in choices:
        try:
            response = session.get(
                "https://opendart.fss.or.kr/api/fnlttXbrl.xml",
                params={"crtfc_key": key, "rcept_no": receipt,
                        "reprt_code": "11011"},
                timeout=(20, 90), stream=True,
            )
            response.raise_for_status()
            content = _read_response_limited(response)
        except XbrlPilotError:
            raise
        except Exception:
            raise XbrlPilotError("official_xbrl_provider_unavailable") from None
        report["provider_xbrl_file_calls"] += 1
        result = inspect_binary_archive(content, year=year)
        missing = {
            "missing_cfo": {"cfo"},
            "missing_both_capex": {"ppe", "intangible"},
            "missing_intangible": {"intangible"},
            "missing_ppe": {"ppe"},
        }[kind]
        report["checked_missing_source_fact_kinds"] += len(missing)
        if result["file_absent"]:
            report["no_original_file"] += 1
            report["missing_source_fact_reasons"]["original_file_absent"] += len(missing)
            continue
        report["valid_zip_files"] += 1
        report["xbrl_instances_detected"] += result["instances"]
        for fact in FACT_NAMES:
            if fact in result["facts"]:
                report["reports_with_ifrs_" + fact + "_tag_candidate"] += 1
        for fact in missing:
            reason = result["annual_krw_reasons"][fact]
            report["missing_source_fact_reasons"][reason] += 1
            if reason == "annual_context_missing":
                shape = result["annual_context_gap_subreasons"][fact]
                report["annual_context_gap_subreasons"][shape] += 1
        if missing.intersection(result["facts"]):
            report["reports_with_any_missing_source_tag"] += 1
        if missing.intersection(result["annual_krw_facts"]):
            report["reports_with_missing_annual_krw_candidate"] += 1
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="docs/data.json")
    parser.add_argument("--catalog", default="docs/v22-market-catalog.json")
    parser.add_argument("--major", default="docs/v3-dart-bulk-history.json")
    parser.add_argument("--state", default="docs/v3-dart-cashflow-reference.json")
    parser.add_argument("--mode", choices=("dry-run", "live"), default="dry-run")
    parser.add_argument("--limit", type=int, default=MAX_PROBES)
    args = parser.parse_args(argv)
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf8"))
    session = None
    if args.mode == "live":
        import requests
        session = requests.Session()
    report = run_pilot(
        read(args.market), read(args.catalog), read(args.major), read(args.state),
        mode=args.mode, key=os.getenv("DART_API_KEY", ""), session=session,
        limit=args.limit,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
