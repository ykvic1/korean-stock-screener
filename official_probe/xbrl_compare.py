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

INSTANCE_NS = 'http://www.xbrl.org/2003/instance'
IX = '{' + INSTANCE_NS + '}'
MAX_ARCHIVE = 25_000_000
MAX_ENTRIES = 120
MAX_XML_FILE = 24_000_000
MAX_TOTAL_EXTRACTED = 75_000_000
MAX_XML_DOCUMENTS = 30
MAX_CONTEXTS = 20000
MAX_FACTS = 150000


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
            if len(xml_files) > MAX_XML_DOCUMENTS:
                raise ProbeError('XBRL archive has too many XML files')
            found = []
            for e in xml_files:
                # zip entry total size is already independently bounded.
                raw = z.read(e)
                root = _safe_xml(raw)
                if root is not None and root.tag == IX + 'xbrl':
                    found.append((e.filename, root))
            return {'zip_sha256': sha256(blob).hexdigest(),
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
        contexts[cid] = (_context_dates(context), dimensioned)
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
        dt, dimensioned = context
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
                facts[key].append({'amount_krw': int(raw),
                                   'dimensioned': bool(dimensioned),
                                   'context_ref': element.get('contextRef'),
                                   'precision': element.get('decimals', 'unknown')})
    return facts


def _compare_metric(api_rows, parsed_facts, metric):
    aliases, sections = ACCOUNTS[metric]
    a = _metric(api_rows, aliases, sections)
    api_value = a['amount_krw']
    all_facts = parsed_facts.get(metric, [])
    # Scope is not established by the presence/absence of a segment.
    # Record only bounded structural counts in the private research artifact,
    # never guess an accounting basis, candidate value or point-in-time status.
    dimensioned_count = sum(bool(f['dimensioned']) for f in all_facts)
    distinct_contexts = len({f['context_ref'] for f in all_facts})
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
    if len(package['instances']) != 1:
        out['status'] = 'xbrl_instance_missing_or_ambiguous'
        return out
    name, root = package['instances'][0]
    out['xbrl_original_archive_observed'] = True
    out['xbrl_instance_file'] = name
    facts = _instance_facts(root, business_year)
    out['metrics'] = {
        metric: _compare_metric(rows, facts, metric) for metric in ACCOUNTS
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
