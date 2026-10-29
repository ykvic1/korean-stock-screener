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
import posixpath
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


def select_candidates(state, major, *, asof, limit=MAX_PROBES,
                      cohort_index=0):
    """Rotate verified, distinct issuer-years across missingness and fiscal years.

    For a fixed cohort index, receipt selection is reproducible. Each next
    cohort rotates fiscal-year strata and then issuer order; no identifiers
    are emitted to the public report. No new receipts or scores are inferred.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_PROBES:
        raise XbrlPilotError("invalid_bounded_xbrl_limit")
    if type(cohort_index) is not int or not 0 <= cohort_index <= 1000000:
        raise XbrlPilotError("invalid_xbrl_cohort_index")
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
    for class_index, kind in enumerate(CLASSES):
        by_year = {}
        for choice in choices[kind]:
            by_year.setdefault(choice[1], []).append(choice)
        years = sorted(by_year, reverse=True)
        if not years:
            continue
        # Spread a four-call cohort across different eligible fiscal years,
        # then advance through issuers inside each year's verified pool.
        # All selection happens *after* receipt validation, never before it.
        fiscal_start = (cohort_index + class_index) % len(years)
        for fiscal_offset in range(len(years)):
            year = years[(fiscal_start + fiscal_offset) % len(years)]
            rows = sorted(by_year[year], key=lambda item: item[0])
            issuer_start = (cohort_index // len(years) + class_index) % len(rows)
            for offset in range(len(rows)):
                choice = rows[(issuer_start + offset) % len(rows)]
                if choice[0] not in used:
                    selected.append(choice)
                    used.add(choice[0])
                    break
            else:
                continue
            break
        if len(selected) == limit:
            break
    return selected


def _recent_archived_receipt_candidates(
        major, state, *, asof, members, limit, cohort_index=0):
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
    return select_candidates(eligible, major, asof=major["asof"],
                             limit=limit, cohort_index=cohort_index)


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



def _annual_krw_evidence(xml_data, year, *, explicit_tags=None):
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
        if "contextRef" not in element.attrib or "unitRef" not in element.attrib:
            continue
        if explicit_tags is None:
            namespace, local = _qualified_name(element.tag)
            if not namespace.startswith((
                "http://xbrl.ifrs.org/taxonomy/",
                "https://xbrl.ifrs.org/taxonomy/",
                "http://www.ifrs.org/",
                "https://www.ifrs.org/",
            )):
                continue
            kinds = [kind for kind, names in FACT_NAMES.items()
                     if local in names]
        else:
            # Research-only exact QName set proven by a local ZIP label link.
            kinds = [kind for kind, tags in explicit_tags.items()
                     if element.tag in tags]
        for kind in kinds:
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


XSD_NS = "http://www.w3.org/2001/XMLSchema"
LINK_NS = "http://www.xbrl.org/2003/linkbase"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
EXACT_CAPEX_LABELS = {
    "ppe": frozenset(("유형자산의취득", "유형자산취득")),
    "intangible": frozenset(("무형자산의취득", "무형자산취득")),
}


def _safe_xbrl_xml(data):
    """Never expand DTD entities or fetch referenced taxonomy URLs."""
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise XbrlPilotError("unsafe_xbrl_xml_entities")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise XbrlPilotError("unparseable_xbrl_archive_member") from None


def _exact_extension_capex_label(text):
    if type(text) is not str or len(text) > 160:
        return None
    label = "".join(text.split())
    matching = [kind for kind, names in EXACT_CAPEX_LABELS.items()
                if label in names]
    return matching[0] if len(matching) == 1 else None


def _local_taxonomy_ref(from_member, href, schemas):
    """Resolve a ZIP-contained schema fragment only; never follow a URL."""
    if (type(href) is not str or len(href) > 320
        or href.count("#") != 1):
        return None
    path, frag = href.split("#")
    if (not path or path.startswith("/") or ":" in path
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,150}", frag)):
        return None
    resolved = posixpath.normpath(posixpath.join(
        posixpath.dirname(from_member), path))
    if resolved == ".." or resolved.startswith("../"):
        return None
    return schemas.get((resolved, frag))


def _extension_label_evidence(archive, entries, *, year=None):
    """Match an exact Korean label -> local schema concept -> instance fact.

    Linked labels are only a discovery lead: the cash-flow statement location,
    amount completeness, consolidation scope and annual KRW context remain
    UNVERIFIED. This function never reads, stores or outputs amounts.
    """
    schemas = {}
    linkbases = []
    instances = set()
    instance_data = []
    schema_files = label_link_files = 0
    for entry in entries:
        if entry.is_dir() or not entry.filename.lower().endswith(
            (".xml", ".xbrl", ".xsd")):
            continue
        root = _safe_xbrl_xml(archive.read(entry))
        if root.tag == "{" + XSD_NS + "}schema":
            schema_files += 1
            namespace = root.get("targetNamespace")
            if not namespace or len(namespace) > 320:
                continue
            for element in root.findall("{" + XSD_NS + "}element"):
                name, ident = element.get("name"), element.get("id")
                if (type(name) is not str or len(name) > 160
                    or type(ident) is not str or len(ident) > 160):
                    continue
                key = (entry.filename, ident)
                if key in schemas and schemas[key] != (namespace, name):
                    raise XbrlPilotError("ambiguous_local_taxonomy_element")
                schemas[key] = (namespace, name)
        if root.tag == "{" + XBRL_INSTANCE_NS + "}xbrl":
            instance_data.append(archive.read(entry))
            for fact in root:
                if "contextRef" in fact.attrib and "unitRef" in fact.attrib:
                    instances.add(fact.tag)
        links = root.findall(".//{" + LINK_NS + "}labelLink")
        if links:
            label_link_files += 1
            linkbases.extend((entry.filename, link) for link in links)
    matched = {"ppe": set(), "intangible": set()}
    label_leads = set()
    xlink = "{" + XLINK_NS + "}"
    for member, link in linkbases:
        locators = {
            loc.get(xlink + "label"): _local_taxonomy_ref(
                member, loc.get(xlink + "href"), schemas)
            for loc in link.findall("{" + LINK_NS + "}loc")
            if loc.get(xlink + "label")
        }
        labels = {}
        for label in link.findall("{" + LINK_NS + "}label"):
            lang = label.get("{" + XML_NS + "}lang", "")
            if lang.lower() not in ("ko", "ko-kr"):
                continue
            kind = _exact_extension_capex_label(label.text)
            if kind and label.get(xlink + "label"):
                labels[label.get(xlink + "label")] = kind
        for arc in link.findall("{" + LINK_NS + "}labelArc"):
            if arc.get(xlink + "arcrole") != (
                "http://www.xbrl.org/2003/arcrole/concept-label"):
                continue
            concept = locators.get(arc.get(xlink + "from"))
            kind = labels.get(arc.get(xlink + "to"))
            if not kind or not concept:
                continue
            namespace, local = concept
            if namespace.startswith((
                "http://xbrl.ifrs.org/", "https://xbrl.ifrs.org/",
                "http://www.ifrs.org/", "https://www.ifrs.org/")):
                continue
            tag = "{" + namespace + "}" + local
            label_leads.add((kind, tag))
            if tag in instances:
                matched[kind].add(tag)
    status = {kind: "label_fact_not_present" for kind in matched}
    extension_context_gaps = {}
    if year is not None:
        checked = {
            kind: {"tag": False, "annual": False, "krw": False,
                   "numeric": False, "values": set(), "context_shapes": set()}
            for kind in FACT_NAMES
        }
        for data in instance_data:
            observed = _annual_krw_evidence(
                data, year, explicit_tags=matched)
            for kind in matched:
                for stage in ("tag", "annual", "krw", "numeric"):
                    checked[kind][stage] |= observed[kind][stage]
                checked[kind]["values"].update(observed[kind]["values"])
                checked[kind]["context_shapes"].update(
                    observed[kind]["context_shapes"])
        reasons = _annual_krw_reasons(checked)
        for kind in matched:
            if len(matched[kind]) > 1:
                status[kind] = "multiple_extension_concepts"
            elif matched[kind]:
                status[kind] = reasons[kind]
                if reasons[kind] == "annual_context_missing":
                    extension_context_gaps[kind] = _context_gap_reason(
                        checked[kind])
    return {
        "local_taxonomy_schema_files": schema_files,
        "local_label_linkbase_files": label_link_files,
        "exact_extension_label_schema_leads": len(label_leads),
        "exact_extension_label_instance_facts": {
            kind: len(tags) for kind, tags in matched.items()},
        "extension_annual_krw_status": status,
        "extension_annual_context_gap_subreasons": extension_context_gaps,
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
                    return {"file_absent": True, "instances": 0, "facts": set(),
                            "annual_krw_facts": set(), "annual_krw_reasons": {},
                            "annual_context_gap_subreasons": {},
                            "extension_label_evidence": None}
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
            # ZIP-bundled labels and schema IDs are checked locally, without
            # following external taxonomy references or interpreting amounts.
            extension = _extension_label_evidence(archive, entries, year=year)
            reasons = _annual_krw_reasons(annual) if year is not None else {}
            candidates = {kind for kind, reason in reasons.items()
                          if reason == "candidate_scope_unverified"}
            return {"file_absent": False, "instances": instances, "facts": facts,
                    "annual_krw_facts": candidates, "annual_krw_reasons": reasons,
                    "annual_context_gap_subreasons": {
                        kind: _context_gap_reason(annual[kind])
                        for kind, reason in reasons.items()
                        if reason == "annual_context_missing"},
                    "extension_label_evidence": extension}
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
              limit=MAX_PROBES, cohort_index=0):
    try:
        if mode not in ("dry-run", "live") or type(limit) is not int or not 1 <= limit <= MAX_PROBES:
            raise XbrlPilotError("invalid_bounded_xbrl_limit_or_mode")
        if type(cohort_index) is not int or not 0 <= cohort_index <= 1000000:
            raise XbrlPilotError("invalid_xbrl_cohort_index")
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
                major, state, asof=asof, members=members, limit=limit,
                cohort_index=cohort_index)
            archived_research = choices is not None
            if choices is None:
                choices = []
        else:
            # Revalidate prior-day observations against the current issuer set
            # and unchanged major filing receipts, with zero provider requests.
            state = retained_current_market_cashflow(market, catalog, major, state)
            verify_state(state, market, catalog, major)
            choices = select_candidates(
                state, major, asof=asof, limit=limit,
                cohort_index=cohort_index)
    except (ValueError, TypeError, KeyError):
        raise XbrlPilotError("saved_xbrl_source_identity_invalid") from None
    report = {
        "schema": "v3-xbrl-archive-capex-structure-pilot-0.8",
        "asof": asof,
        "mode": mode,
        "sample_cohort_mode": "verified_receipt_year_stratified_rotation",
        "sample_cohort_index": cohort_index,
        "sample_cohort_distinct_issuers": len({row[0] for row in choices}),
        "sample_fiscal_year_counts": {
            str(year): sum(choice[1] == year for choice in choices)
            for year in YEARS
        },
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
        "local_taxonomy_schema_files": 0,
        "local_label_linkbase_files": 0,
        "exact_extension_label_schema_leads": 0,
        "reports_with_extension_ppe_label_fact": 0,
        "reports_with_extension_intangible_label_fact": 0,
        "extension_candidate_validation_reasons": {
            reason: 0 for reason in (
                "multiple_extension_concepts", "annual_context_missing",
                "krw_unit_missing", "numeric_fact_invalid",
                "numeric_values_ambiguous", "candidate_scope_unverified")
        },
        "reports_with_extension_ppe_annual_krw_candidate": 0,
        "reports_with_extension_intangible_annual_krw_candidate": 0,
        "extension_annual_context_gap_subreasons": {
            shape: 0 for shape in CONTEXT_GAP_PRIORITY
        },
        "extension_leads_never_certify_cashflow_amount_or_scope": True,
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
        extension = result["extension_label_evidence"]
        report["local_taxonomy_schema_files"] += extension["local_taxonomy_schema_files"]
        report["local_label_linkbase_files"] += extension["local_label_linkbase_files"]
        report["exact_extension_label_schema_leads"] += extension[
            "exact_extension_label_schema_leads"]
        for kind in ("ppe", "intangible"):
            if extension["exact_extension_label_instance_facts"][kind]:
                report["reports_with_extension_" + kind + "_label_fact"] += 1
                status = extension["extension_annual_krw_status"][kind]
                report["extension_candidate_validation_reasons"][status] += 1
                if status == "annual_context_missing":
                    shape = extension[
                        "extension_annual_context_gap_subreasons"][kind]
                    report["extension_annual_context_gap_subreasons"][shape] += 1
                if status == "candidate_scope_unverified":
                    report["reports_with_extension_" + kind +
                           "_annual_krw_candidate"] += 1
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
    parser.add_argument("--cohort-index", type=int, default=0,
                        help="Nonnegative bounded research cohort (0..1000000)")
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
        limit=args.limit, cohort_index=args.cohort_index,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
