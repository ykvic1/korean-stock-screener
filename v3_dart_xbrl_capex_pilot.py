"""Read-only, receipt-pinned OpenDART XBRL archive structural evidence pilot.

This does NOT recover a CAPEX amount, financial score, PIT evidence or XBRL
statement scope. Four bounded official file calls at most. Only aggregate
booleans/counts are printed; source archives and issuer identities are never
written to disk or public workflow logs.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from v3_dart_bulk_history import _inputs, _day, _valid_receipt
from v3_dart_cashflow_enrichment import YEARS, verify_state

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



def _annual_krw_facts(xml_data, year):
    """Annual-period KRW candidate tags only; statement scope still unknown.

    The exact company is pinned to the filing receipt by the caller. A simple
    XBRL context can be individual or consolidated, so never promote this
    candidate into the source cashflow store or investment score.
    """
    if year not in YEARS:
        raise XbrlPilotError("unsupported_xbrl_fiscal_year")
    if b"<!DOCTYPE" in xml_data.upper() or b"<!ENTITY" in xml_data.upper():
        raise XbrlPilotError("unsafe_xbrl_xml_entities")
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        raise XbrlPilotError("unparseable_xbrl_instance") from None
    if root.tag != "{" + XBRL_INSTANCE_NS + "}xbrl":
        return set()
    ns = "{" + XBRL_INSTANCE_NS + "}"
    units = set()
    for unit in root.findall(ns + "unit"):
        measures = unit.findall(ns + "measure")
        if (len(measures) == 1 and measures[0].text is not None
            and measures[0].text.strip() == "iso4217:KRW"
            and type(unit.get("id")) is str):
            units.add(unit.get("id"))
    contexts = set()
    for context in root.findall(ns + "context"):
        if (context.find(ns + "segment") is not None
            or context.find(ns + "scenario") is not None
            or context.find(ns + "entity/" + ns + "segment") is not None):
            continue
        periods = context.findall(ns + "period")
        if len(periods) != 1:
            continue
        start = periods[0].findtext(ns + "startDate")
        end = periods[0].findtext(ns + "endDate")
        if (start == str(year) + "-01-01"
            and end == str(year) + "-12-31"
            and type(context.get("id")) is str):
            contexts.add(context.get("id"))
    amounts = {kind: [] for kind in FACT_NAMES}
    for element in root:
        namespace, local = _qualified_name(element.tag)
        if not namespace.startswith((
            "http://xbrl.ifrs.org/taxonomy/",
            "https://xbrl.ifrs.org/taxonomy/",
            "http://www.ifrs.org/",
            "https://www.ifrs.org/",
        )):
            continue
        if (element.get("contextRef") not in contexts
            or element.get("unitRef") not in units
            or element.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"):
            continue
        raw = (element.text or "").strip()
        if not re.fullmatch(r"-?[0-9]{1,18}", raw):
            continue
        for kind, names in FACT_NAMES.items():
            if local in names and (kind == "cfo" or int(raw) >= 0):
                amounts[kind].append(int(raw))
    return {
        kind for kind, values in amounts.items()
        if values and len(set(values)) == 1
    }

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
                    return {"file_absent": True, "instances": 0, "facts": set(), "annual_krw_facts": set()}
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
            annual = set()
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
                    annual.update(_annual_krw_facts(data, year))
            return {"file_absent": False, "instances": instances, "facts": facts,
                    "annual_krw_facts": annual}
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
        verify_state(state, market, catalog, major)
        asof, members = _inputs(market, catalog)
        choices = select_candidates(state, major, asof=asof, limit=limit)
    except (ValueError, TypeError, KeyError):
        raise XbrlPilotError("saved_xbrl_source_identity_invalid") from None
    report = {
        "schema": "v3-xbrl-archive-capex-structure-pilot-0.2",
        "asof": asof,
        "mode": mode,
        "eligible_sampled_issuer_years": len(choices),
        "provider_xbrl_file_calls": 0,
        "valid_zip_files": 0,
        "no_original_file": 0,
        "xbrl_instances_detected": 0,
        "reports_with_ifrs_cfo_tag_candidate": 0,
        "reports_with_ifrs_ppe_tag_candidate": 0,
        "reports_with_ifrs_intangible_tag_candidate": 0,
        "reports_with_any_missing_source_tag": 0,
        "reports_with_missing_annual_krw_candidate": 0,
        "missing_not_interpreted_as_zero": True,
        "no_statement_scope_verified": True,
        "annual_krw_facts_not_scored": True,
        "no_xbrl_amounts_or_investment_scores_derived": True,
        "no_issuer_or_receipt_or_raw_xbrl_published": True,
        "note": "Annual KRW candidate tags only; financial statement scope unverified.",
    }
    if mode == "dry-run":
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
        if result["file_absent"]:
            report["no_original_file"] += 1
            continue
        report["valid_zip_files"] += 1
        report["xbrl_instances_detected"] += result["instances"]
        for fact in FACT_NAMES:
            if fact in result["facts"]:
                report["reports_with_ifrs_" + fact + "_tag_candidate"] += 1
        missing = {
            "missing_cfo": {"cfo"},
            "missing_both_capex": {"ppe", "intangible"},
            "missing_intangible": {"intangible"},
            "missing_ppe": {"ppe"},
        }[kind]
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
