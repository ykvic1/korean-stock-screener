"""Conservative one-report OpenDART XBRL vs annual API numeric crosscheck.

The package from fnlttXbrl.xml is tied to a filing receipt. We compare only
directly identifiable XBRL facts with exact fiscal period, KRW unit and known
taxonomy concepts. We NEVER infer CFS/OFS from an undimensioned XBRL context
and NEVER mark historical point-in-time eligibility.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from .clients import ProbeError, canonical_date
from .financial import ACCOUNTS, _metric
from .revisions import audit_annual_revisions
from .embedded_taxonomy import (
    extract_embedded_role_definitions, summarize_role_definition,
    MAX_EMBEDDED_XSD,
)

INSTANCE_NS = 'http://www.xbrl.org/2003/instance'
IX = '{' + INSTANCE_NS + '}'
MAX_ARCHIVE = 25_000_000
MAX_ENTRIES = 120
MAX_XML_FILE = 24_000_000
MAX_TOTAL_EXTRACTED = 75_000_000
MAX_XML_DOCUMENTS = 30
MAX_CONTEXTS = 20000
MAX_FACTS = 150000
XBRLDI = '{http://xbrl.org/2006/xbrldi}'
XLINK = '{http://www.w3.org/1999/xlink}'
LINK = '{http://www.xbrl.org/2003/linkbase}'
MAX_RESEARCH_AXIS_SAMPLES = 16
MAX_PRESENTATION_ROLE_EVIDENCE = 32
REVENUE_CONCEPTS = frozenset(('Revenue', 'RevenueFromContractsWithCustomers'))


def _safe_qname_local(raw):
    """Whitelisted local QName fragment, NOT a namespace or accounting proof."""
    if not isinstance(raw, str):
        return 'unrecognized'
    value = raw.strip()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,79}(?::[A-Za-z_][A-Za-z0-9_.-]{0,79})?', value):
        return 'unrecognized'
    return value.rsplit(':', 1)[-1]


def _context_axis_members(context):
    """Read bounded explicit/typed dimension metadata; never retain typed values."""
    members = set()
    for node in context.iter():
        if node.tag == XBRLDI + 'explicitMember':
            members.add(('explicit',
                         _safe_qname_local(node.get('dimension')),
                         _safe_qname_local(node.text)))
        elif node.tag == XBRLDI + 'typedMember':
            members.add(('typed',
                         _safe_qname_local(node.get('dimension')),
                         'typed_value_hidden'))
    return tuple(sorted(members)[:MAX_RESEARCH_AXIS_SAMPLES])



def _concept(tag: str):
    if not isinstance(tag, str) or not tag.startswith('{'):
        return None
    uri, _, local = tag[1:].partition('}')
    low = uri.lower()
    if 'ifrs' in low and local in (
        'Revenue', 'RevenueFromContractsWithCustomers',
        'CashFlowsFromUsedInOperatingActivities', 'Assets',
        'Liabilities', 'Equity'):
        return local
    if ('dart' in low or 'fss.or.kr' in low) and local == 'OperatingIncomeLoss':
        return local
    return None


def _safe_xml(content: bytes):
    upper = content.upper()
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
        raise ProbeError('DTD/entities are forbidden in research XBRL')
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None



def _local_presentation_revenue_hints(root):
    """Embedded presentation arc/role evidence; not a primary-statement verdict.

    A single concept may appear in BOTH primary statements and notes, in
    multiple roles. Role URI or label alone cannot certify a statement.
    """
    if root.tag != LINK + 'linkbase':
        return None
    counts = {k: 0 for k in REVENUE_CONCEPTS}
    per_role = {}
    relation_groups = {}
    links = root.findall(LINK + 'presentationLink')
    if len(links) > MAX_FACTS:
        raise ProbeError('Excessive local XBRL presentation links')
    for link in links:
        role_uri = link.get(XLINK + 'role') or ''
        role_digest = sha256(role_uri.encode('utf-8')).hexdigest()[:16]
        # Human-auditable evidence only: bounded and sanitised URI last part.
        role_tail = _safe_qname_local(role_uri.rsplit('/', 1)[-1]
                                      .rsplit('#', 1)[-1])
        labels = {}
        local_names = {}
        for loc in link.findall(LINK + 'loc'):
            name = (loc.get(XLINK + 'href') or '').split('#')[-1]
            label = loc.get(XLINK + 'label')
            if not isinstance(label, str) or len(label) > 100:
                continue
            local_names[label] = _safe_qname_local(
                name.split('_', 1)[-1] if '_' in name else name)
            if '_' in name:
                tail = name.split('_', 1)[-1]
                if tail in REVENUE_CONCEPTS:
                    labels[label] = tail
        arcs = link.findall(LINK + 'presentationArc')
        if len(arcs) > MAX_FACTS:
            raise ProbeError('Excessive embedded presentation arcs')
        for arc in arcs:
            src, dst = arc.get(XLINK + 'from'), arc.get(XLINK + 'to')
            for concept in {labels.get(src), labels.get(dst)} & REVENUE_CONCEPTS:
                counts[concept] += 1
                key = (role_uri, role_tail, concept)
                per_role[key] = per_role.get(key, 0) + 1
                record = relation_groups.setdefault(
                    key, {'as_parent_arc_count': 0, 'as_child_arc_count': 0,
                          'direct_parent_names': set()})
                if labels.get(dst) == concept:
                    record['as_child_arc_count'] += 1
                    record['direct_parent_names'].add(
                        local_names.get(src, 'unrecognized'))
                if labels.get(src) == concept:
                    record['as_parent_arc_count'] += 1
    return {'embedded_presentation_links': len(links),
            'revenue_presentation_arc_hints': counts,
            'revenue_role_arc_groups': per_role,
            'revenue_relation_groups': relation_groups}



def _instance_from_zip(blob: bytes):
    if not 10 <= len(blob) <= MAX_ARCHIVE:
        raise ProbeError('XBRL response outside ZIP size limits')
    try:
        with ZipFile(BytesIO(blob)) as z:
            entries = [e for e in z.infolist() if not e.is_dir()]
            if not entries or len(entries) > MAX_ENTRIES:
                raise ProbeError('XBRL archive has unexpected entry count')
            total = 0
            xml_files = []
            xsd_files = []
            seen = set()
            for e in entries:
                name = e.filename
                pieces = name.replace('\\', '/').split('/')
                if (name.startswith('/') or '\\' in name or '..' in pieces
                        or not name or name in seen):
                    raise ProbeError('XBRL archive filename is unsafe')
                seen.add(name)
                if e.file_size > MAX_XML_FILE:
                    raise ProbeError('XBRL uncompressed entry exceeds limit')
                total += e.file_size
                if total > MAX_TOTAL_EXTRACTED:
                    raise ProbeError('XBRL uncompressed archive exceeds limit')
                if name.lower().endswith(('.xbrl', '.xml')):
                    xml_files.append(e)
                elif name.lower().endswith('.xsd'):
                    xsd_files.append(e)
            if len(xsd_files) > MAX_EMBEDDED_XSD:
                raise ProbeError('Excessive embedded XBRL schema files')
            if len(xml_files) > MAX_XML_DOCUMENTS:
                raise ProbeError('XBRL archive has too many XML files')
            found = []
            embedded_presentation_files = 0
            embedded_presentation_links = 0
            revenue_arc_hints = {k: 0 for k in REVENUE_CONCEPTS}
            revenue_role_groups = {}
            revenue_relation_groups = {}
            role_definitions = {}
            for e in xsd_files:
                schema_root = _safe_xml(z.read(e))
                if schema_root is not None:
                    extract_embedded_role_definitions(schema_root,
                                                      role_definitions)
            for e in xml_files:
                # zip entry total size is already independently bounded.
                raw = z.read(e)
                root = _safe_xml(raw)
                if root is not None and root.tag == IX + 'xbrl':
                    found.append((e.filename, root))
                if root is not None:
                    hints = _local_presentation_revenue_hints(root)
                    if hints is not None:
                        embedded_presentation_files += 1
                        embedded_presentation_links += hints['embedded_presentation_links']
                        for k, v in hints['revenue_presentation_arc_hints'].items():
                            revenue_arc_hints[k] += v
                        for key, count in hints['revenue_role_arc_groups'].items():
                            revenue_role_groups[key] = (
                                revenue_role_groups.get(key, 0) + count)
                        for key, relation in hints['revenue_relation_groups'].items():
                            target = revenue_relation_groups.setdefault(
                                key, {'as_parent_arc_count': 0,
                                      'as_child_arc_count': 0,
                                      'direct_parent_names': set()})
                            target['as_parent_arc_count'] += relation['as_parent_arc_count']
                            target['as_child_arc_count'] += relation['as_child_arc_count']
                            target['direct_parent_names'].update(
                                relation['direct_parent_names'])
            # This evidence is not a resolved taxonomy set. Roles could come
            # from financial statement OR notes; do not select any fact.
            all_roles = sorted(revenue_role_groups.items())
            role_samples = []
            for ((role_uri, role_tail, concept), count) in (
                    all_roles[:MAX_PRESENTATION_ROLE_EVIDENCE]):
                relation = revenue_relation_groups[(role_uri, role_tail, concept)]
                role_samples.append({
                    'role_uri_sha256_prefix':
                        sha256(role_uri.encode('utf-8')).hexdigest()[:16],
                    'role_uri_tail': role_tail,
                    'concept_local': concept,
                    'presentation_arc_count': count,
                    'as_child_arc_count': relation['as_child_arc_count'],
                    'as_parent_arc_count': relation['as_parent_arc_count'],
                    'direct_parent_concept_local_samples': sorted(
                        relation['direct_parent_names'])[:8],
                    'parent_sample_truncated': len(relation['direct_parent_names']) > 8,
                    **summarize_role_definition(role_uri, role_definitions),
                })
            return {'embedded_presentation_files': embedded_presentation_files,
                    'embedded_presentation_links': embedded_presentation_links,
                    'revenue_presentation_arc_hints': revenue_arc_hints,
                    'revenue_presentation_role_samples': role_samples,
                    'embedded_xsd_file_count': len(xsd_files),
                    'embedded_role_type_count': len(role_definitions),
                    'embedded_role_type_definitions_complete_dts_verified': False,
                    'revenue_presentation_role_samples_truncated':
                        len(all_roles) > MAX_PRESENTATION_ROLE_EVIDENCE,
                    'zip_sha256': sha256(blob).hexdigest(),
                    'archive_entries': len(entries),
                    'xml_entries': len(xml_files),
                    'instances': found}
    except (BadZipFile, EOFError, RuntimeError, OSError, ValueError):
        raise ProbeError('XBRL response is not a safe readable ZIP') from None


def _context_dates(context):
    period = context.find(IX + 'period')
    if period is None:
        return None
    instant = period.find(IX + 'instant')
    start = period.find(IX + 'startDate')
    end = period.find(IX + 'endDate')
    if instant is not None:
        try:
            return ('instant', date.fromisoformat((instant.text or '').strip()).isoformat())
        except ValueError:
            return None
    if start is not None and end is not None:
        try:
            return ('duration',
                    date.fromisoformat((start.text or '').strip()).isoformat(),
                    date.fromisoformat((end.text or '').strip()).isoformat())
        except ValueError:
            return None
    return None


def _instance_facts(root, business_year):
    contexts = {}
    for context in root.findall(IX + 'context'):
        cid = context.get('id')
        if not cid or cid in contexts:
            raise ProbeError('Ambiguous or missing XBRL context identifier')
        entity = context.find(IX + 'entity')
        # Segmented, scenario and dimensioned facts may be CFS / OFS / notes.
        dimensioned = (entity is not None and entity.find(IX + 'segment') is not None
                       or context.find(IX + 'scenario') is not None)
        contexts[cid] = (_context_dates(context), dimensioned,
                         _context_axis_members(context))
        if len(contexts) > MAX_CONTEXTS:
            raise ProbeError('Excessive XBRL contexts')
    units = {}
    for unit in root.findall(IX + 'unit'):
        uid = unit.get('id')
        if not uid or uid in units:
            raise ProbeError('Ambiguous or missing XBRL unit')
        measures = unit.findall(IX + 'measure')
        units[uid] = (len(measures) == 1 and
                      (measures[0].text or '').strip() == 'iso4217:KRW' and
                      len(list(unit)) == 1)
    expected_duration = ('duration', f'{business_year}-01-01', f'{business_year}-12-31')
    expected_instant = ('instant', f'{business_year}-12-31')
    facts = {key: [] for key in ACCOUNTS}
    scanned = 0
    for element in root:
        scanned += 1
        if scanned > MAX_FACTS:
            raise ProbeError('Excessive XBRL facts')
        local = _concept(element.tag)
        if not local:
            continue
        context = contexts.get(element.get('contextRef'))
        if context is None or not units.get(element.get('unitRef'), False):
            continue
        dt, dimensioned, axes = context
        is_balance = local in ('Assets', 'Liabilities', 'Equity')
        if dt != (expected_instant if is_balance else expected_duration):
            continue
        raw = (element.text or '').strip()
        if element.attrib.get('{http://www.w3.org/2001/XMLSchema-instance}nil') == 'true':
            continue
        if not re.fullmatch(r'[+-]?\d+', raw):
            continue
        for key, (aliases, _) in ACCOUNTS.items():
            acceptable = {a.split('_', 1)[1] for a in aliases}
            if local in acceptable:
                # Concept local name and namespace digest let a researcher
                # distinguish IFRS Revenue from RevenueFromContractsWithCustomers,
                # without persisting raw taxonomy URLs or any raw XBRL payload.
                namespace = element.tag[1:].split('}', 1)[0]
                facts[key].append({'amount_krw': int(raw),
                                   'concept_local': local,
                                   'concept_namespace_digest': sha256(
                                       namespace.encode('utf-8')).hexdigest()[:16],
                                   'dimensioned': bool(dimensioned),
                                   'context_ref': element.get('contextRef'),
                                   'dimension_axes': axes,
                                   'precision': element.get('decimals', 'unknown')})
    return facts


def _compare_metric(api_rows, parsed_facts, metric, fs_div):
    # One observed DART label pattern from the prior OFS sample. QName locals
    # do NOT establish the taxonomy namespace, presentation role, or primary
    # statement membership. Never promote this heuristic into validation.
    research_pattern = (
        ('explicit', 'ConsolidatedAndSeparateFinancialStatementsAxis',
         'SeparateMember') if fs_div == 'OFS' else None
    )
    aliases, sections = ACCOUNTS[metric]
    a = _metric(api_rows, aliases, sections)
    api_value = a['amount_krw']
    all_facts = parsed_facts.get(metric, [])
    scope_pattern_candidates = (
        [f for f in all_facts
         if tuple(f.get('dimension_axes', ())) == (research_pattern,)
         and f.get('dimensioned', False)]
        if research_pattern is not None else None)
    # Distinguish alternate taxonomy concepts from duplicate facts sharing
    # one concept/context. The totals are strictly diagnostic and unverified.
    # Reference names are limited to _concept's vetted account vocabulary.
    scope_by_concept = {}
    if scope_pattern_candidates is not None:
        for fact in scope_pattern_candidates:
            key = (fact.get('concept_local', 'unrecognized'),
                   fact.get('concept_namespace_digest', 'unknown'))
            scope_by_concept.setdefault(key, []).append(fact)
    concept_breakdown = []
    for (concept, namespace_digest), grouped in sorted(scope_by_concept.items()):
        concept_breakdown.append({
            'concept_local': concept,
            'namespace_sha256_prefix': namespace_digest,
            'fact_count': len(grouped),
            'api_amount_equal_count': (
                sum(f['amount_krw'] == api_value for f in grouped)
                if api_value is not None else None),
            'distinct_context_count': len({f['context_ref'] for f in grouped}),
            'distinct_value_count': len({f['amount_krw'] for f in grouped}),
        })
    if scope_pattern_candidates is None:
        duplicate_kind = 'scope_pattern_not_supported_for_requested_basis'
    elif not scope_pattern_candidates:
        duplicate_kind = 'no_scope_pattern_only_candidates'
    elif len(scope_pattern_candidates) == 1:
        duplicate_kind = 'single_scope_pattern_only_candidate'
    elif len(scope_by_concept) > 1:
        duplicate_kind = 'multiple_taxonomy_concepts_or_namespaces'
    elif len({f['context_ref'] for f in scope_pattern_candidates}) > 1:
        duplicate_kind = 'same_concept_distinct_contexts'
    else:
        duplicate_kind = 'same_concept_same_context_multiple_facts'
    # Scope is not established by the presence/absence of a segment.
    # Record only bounded structural counts in the private research artifact,
    # never guess an accounting basis, candidate value or point-in-time status.
    dimensioned_count = sum(bool(f['dimensioned']) for f in all_facts)
    distinct_contexts = len({f['context_ref'] for f in all_facts})
    axis_counts = {}
    for fact in all_facts:
        for axis in fact.get('dimension_axes', ()):
            axis_counts[axis] = axis_counts.get(axis, 0) + 1
    # Only axis/member names and counts, never raw context ids or XBRL fact values.
    axis_samples = [
        {'kind': k, 'axis_local': a, 'member_local': m, 'candidate_fact_count': n}
        for (k, a, m), n in sorted(axis_counts.items())[:MAX_RESEARCH_AXIS_SAMPLES]
    ]
    ambiguity_reasons = []
    if len(all_facts) > 1:
        ambiguity_reasons.append('multiple_candidate_facts')
    if distinct_contexts > 1:
        ambiguity_reasons.append('multiple_xbrl_contexts')
    if dimensioned_count:
        ambiguity_reasons.append('segment_or_scenario_present')
    base = {'api_amount_krw': api_value, 'xbrl_amount_krw': None,
            'candidate_fact_count': len(all_facts),
            'xbrl_dimensioned_fact_count': dimensioned_count,
            'xbrl_undimensioned_fact_count': len(all_facts) - dimensioned_count,
            'xbrl_distinct_context_count': distinct_contexts,
            'xbrl_candidate_api_amount_equal_count': (
                sum(f['amount_krw'] == api_value for f in all_facts)
                if api_value is not None else None),
            'xbrl_requested_scope_pattern_only_candidate_count': (
                len(scope_pattern_candidates)
                if scope_pattern_candidates is not None else None),
            'xbrl_requested_scope_pattern_only_api_equal_count': (
                sum(f['amount_krw'] == api_value for f in scope_pattern_candidates)
                if scope_pattern_candidates is not None and api_value is not None
                else None),
            'xbrl_requested_scope_pattern_is_verified': False,
            'xbrl_scope_pattern_concept_groups': concept_breakdown,
            'xbrl_scope_pattern_duplicate_kind': duplicate_kind,
            'xbrl_taxonomy_concept_meaning_verified': False,
            'xbrl_presentation_statement_role_verified': False,
            'xbrl_dimension_axis_samples': axis_samples,
            'xbrl_unparsed_dimensioned_fact_count': sum(
                bool(f['dimensioned']) and not f.get('dimension_axes')
                for f in all_facts),
            'xbrl_axis_samples_truncated': len(axis_counts) > MAX_RESEARCH_AXIS_SAMPLES,
            'xbrl_ambiguity_reasons': ambiguity_reasons,
            'xbrl_accounting_scope_verified': False,
            'historical_numeric_snapshot_verified': False,
            'scoring_allowed': False}
    if api_value is None:
        return {**base, 'status': 'api_account_missing_or_ambiguous'}
    if not all_facts:
        return {**base, 'status': 'matching_xbrl_fact_absent'}
    # Multiple contexts, even if they happen to share a value, do not prove
    # which values correspond to separate/consolidated primary statements.
    if len(all_facts) != 1 or all_facts[0]['dimensioned']:
        return {**base, 'status': 'xbrl_fact_or_scope_ambiguous'}
    fact = all_facts[0]
    value = fact['amount_krw']
    return {**base, 'xbrl_amount_krw': value,
            'xbrl_context_ref': fact['context_ref'],
            'xbrl_decimals': fact['precision'],
            'status': ('equal_observed_scope_unverified' if value == api_value
                       else 'different_observed_scope_unverified')}


def compare_annual_xbrl(client, corp_code: str, business_year: int, asof: str,
                        receipt: str, fs_div: str = 'OFS'):
    """One receipt history request, one financial JSON, one XBRL ZIP, max."""
    if not re.fullmatch(r'\d{8}', str(corp_code)):
        raise ValueError('corp_code must have 8 digits')
    if not isinstance(business_year, int) or not 2015 <= business_year <= 2100:
        raise ValueError('unsupported business_year')
    if not re.fullmatch(r'\d{14}', str(receipt)):
        raise ValueError('XBRL receipt must have 14 digits')
    if fs_div not in ('CFS', 'OFS'):
        raise ValueError('fs_div must be CFS or OFS')
    canonical_date(asof)
    out = {
        'business_year': business_year, 'asof': asof, 'corp_code': corp_code,
        'rcept_no': receipt, 'report_code': '11011', 'fs_div': fs_div,
        'status': 'unverified', 'metrics': None,
        'receipt_latest_asof': False, 'financial_api_receipt_matches': False,
        'xbrl_original_archive_observed': False,
        'all_six_values_equal_as_observed': False,
        'financial_numeric_changes_verified': False,
        'xbrl_accounting_scope_verified': False,
        'historical_numeric_snapshot_verified': False,
        'revision_history_validated': False,
        'asof_join_validated': False,
        'confirmed_scoring_allowed': False, 'investment_data_ready': False,
        'warnings': [
            'XBRL context does not prove consolidated/separate reporting basis.',
            'Current financial API numbers may be revised after original filing.',
            'No single-day receipt-time or historical investment eligibility verified.',
            'A numeric match does not authorize investment scoring or backtests.',
        ],
    }
    timeline = audit_annual_revisions(client, corp_code, business_year,
                                      asof, receipt, download_documents=False)
    out['filing_list_status'] = timeline['status']
    out['receipt_latest_asof'] = timeline['expected_receipt_latest_asof']
    out['amendment_observed'] = timeline['amendment_observed']
    # This is strictly *calendar-date* visibility from the bounded filing
    # list, not historical financial-value PIT verification, nor a filing
    # timestamp sufficient for trading on the filing day.
    out['original_receipt_precedes_asof_date_only'] = bool(
        timeline['revision_listing_complete']
        and timeline['expected_receipt_latest_asof']
        and not timeline['amendment_observed'])
    out['filing_availability_granularity'] = 'calendar_date_only'
    out['financial_api_is_historical_snapshot_verified'] = False
    if (not timeline['revision_listing_complete']
            or not timeline['expected_receipt_latest_asof']
            or timeline['amendment_observed']):
        out['status'] = 'revision_history_blocks_single_report_comparison'
        return out

    result = client._api(
        'fnlttSinglAcntAll.json', corp_code=corp_code,
        bsns_year=str(business_year), reprt_code='11011', fs_div=fs_div)
    if result.get('status') == '013':
        out['status'] = 'financial_api_no_data'
        return out
    rows = result.get('list')
    if not isinstance(rows, list) or not rows:
        out['status'] = 'financial_api_rows_unavailable'
        return out
    # fs_div is a required *request* parameter, but not a documented
    # fnlttSinglAcntAll response field. An omitted field is acceptable only
    # for this identity gate; it does NOT independently verify OFS/CFS scope.
    out['financial_api_fs_div_explicit_in_rows'] = all(
        isinstance(row, dict) and 'fs_div' in row for row in rows)
    if not all(isinstance(row, dict)
               and str(row.get('corp_code', corp_code)) == corp_code
               and str(row.get('rcept_no', '')) == receipt
               and str(row.get('bsns_year', '')) == str(business_year)
               and str(row.get('reprt_code', '')) == '11011'
               and ('fs_div' not in row or str(row['fs_div']) == fs_div)
               for row in rows):
        out['status'] = 'financial_api_report_identity_mismatch'
        return out
    out['financial_api_receipt_matches'] = True
    out['financial_api_rows'] = len(rows)
    out['financial_api_sha256'] = sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')).encode('utf-8')).hexdigest()

    raw = client.original_xbrl_zip(receipt, '11011')
    package = _instance_from_zip(raw)
    out['xbrl_zip_sha256'] = package['zip_sha256']
    out['xbrl_archive_entries'] = package['archive_entries']
    out['xbrl_xml_entries'] = package['xml_entries']
    out['xbrl_instances_found'] = len(package['instances'])
    out['embedded_presentation_files_observed'] = package['embedded_presentation_files']
    out['embedded_presentation_links_observed'] = package['embedded_presentation_links']
    out['revenue_embedded_presentation_arc_hints'] = package['revenue_presentation_arc_hints']
    out['revenue_embedded_presentation_role_samples'] = package[
        'revenue_presentation_role_samples']
    out['embedded_xsd_file_count'] = package['embedded_xsd_file_count']
    out['embedded_role_type_count'] = package['embedded_role_type_count']
    out['embedded_role_type_definitions_complete_dts_verified'] = False
    out['revenue_embedded_presentation_role_samples_truncated'] = package[
        'revenue_presentation_role_samples_truncated']
    out['presentation_role_names_are_only_clues'] = True
    out['presentation_linkbase_complete_dts_verified'] = False
    out['primary_statement_role_verified'] = False
    if len(package['instances']) != 1:
        out['status'] = 'xbrl_instance_missing_or_ambiguous'
        return out
    name, root = package['instances'][0]
    out['xbrl_original_archive_observed'] = True
    out['xbrl_instance_file'] = name
    facts = _instance_facts(root, business_year)
    out['metrics'] = {
        metric: _compare_metric(rows, facts, metric, fs_div) for metric in ACCOUNTS
    }
    matches = sum(v['status'] == 'equal_observed_scope_unverified'
                  for v in out['metrics'].values())
    mismatches = sum(v['status'] == 'different_observed_scope_unverified'
                     for v in out['metrics'].values())
    out['equal_observed_count'] = matches
    out['different_observed_count'] = mismatches
    out['all_six_values_equal_as_observed'] = matches == len(ACCOUNTS)
    out['status'] = 'six_values_observed_equal_scope_unverified' if (
        matches == len(ACCOUNTS)) else 'partial_or_different_values_unverified'
    return out
