"""Receipt-deduplicated whole-report XML recovery pilot (no score promotion).

Distinct OpenDART document.xml source, not the exhausted XBRL account APIs.
Only compact candidate facts, source hashes and negative review markers are
persisted; no raw archive, document text, table or credentials are published.
Candidates require explicit labels, exact KRW units and independent anchors.
They still require financial-statement reconciliation before admission.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unicodedata import category
from zipfile import ZipFile, BadZipFile
from xml.parsers.expat import errors as xml_errors
import xml.etree.ElementTree as ET

SCHEMA = 'v3-dart-whole-report-candidate-pilot-v1'
OUT = 'v3-dart-whole-report-candidates.json'
LIMIT = 30
MAX_ZIP = 30_000_000
MAX_EXPANDED = 150_000_000
RECEIPT = re.compile(r'[0-9]{14}\Z')
LABELS = {
    'cfo': {'영업활동현금흐름', '영업활동으로인한현금흐름'},
    'ppe': {'유형자산의취득', '유형자산취득'},
    'cip': {'건설중인자산의취득', '건설중인자산취득'},
    'intangible': {'무형자산의취득', '무형자산취득'},
    'investing': {'투자활동현금흐름', '투자활동으로인한현금흐름'},
    'revenue': {'매출액', '수익(매출액)', '영업수익'},
    'operating_income': {'영업이익', '영업이익(손실)', '영업손익'},
    'owners_profit': {'지배기업의소유주에게귀속되는당기순이익',
                      '지배기업소유주지분순이익', '지배기업소유주귀속당기순이익',
                      '지배기업의소유주지분', '지배기업소유주지분',
                      '지배기업의소유주에게귀속되는당기순이익(손실)',
                      '지배기업의소유주에게귀속되는당기순손익'},
    'eps': {'기본주당이익', '기본주당순이익', '기본주당이익(손실)',
            '기본주당순이익(손실)', '기본주당손익'},
    'equity': {'자본총계'},
    'liabilities': {'부채총계'},
    'owners_equity': {'지배기업의소유주에게귀속되는자본',
                      '지배기업소유주지분', '지배기업의소유주지분',
                      '지배기업의소유주에게귀속되는지분'},
}
FIN = {'cashflow_fcf_persistence','fcf_yield','per_eps_quality_growth',
       'pbr_equity_quality','revenue_growth','operating_growth_margin',
       'sales_to_cap','balance_sheet_strength'}
SEEDS = ('018120','210540','205500')
NOTE_READER = 'attributable_profit_eps_note_v7'
NOTE_RECEIPT = '20260316001465'

class PilotError(ValueError):
    pass

def norm(value):
    return re.sub(r'\s+', '', value).replace('ㆍ','').replace('·','')

def tag(node):
    return node.tag.rsplit('}',1)[-1].upper() if isinstance(node.tag,str) else ''

def value(node):
    return ''.join(node.itertext()).strip()

def number(s):
    s = norm(s).replace(',', '').replace('−','-')
    if re.fullmatch(r'\([0-9]+\)',s):
        s = '-' + s[1:-1]
    # Explicit zero is valid. A blank/dash is not evidence of zero.
    if not re.fullmatch(r'-?[0-9]+',s):
        return None
    n = int(s)
    return n if abs(n) <= 10**18 else None

def table_rows(table):
    rows=[]
    for row in table.iter():
        if tag(row) != 'TR':
            continue
        cells=[value(c) for c in row if tag(c) in ('TD','TH','TE')]
        if cells:
            rows.append(cells)
    return rows

def declared_units(cells):
    return {unit for cell in cells
            for unit in re.findall(r'단위[:：]([^()]+)(?:\)|$)', norm(cell))}

def extract_rows(rows):
    result={}
    for cells in rows:
        label=norm(cells[0])
        for field, labels in LABELS.items():
            if label in labels:
                vals=tuple(number(c) for c in cells[1:])
                if any(v is not None for v in vals):
                    result.setdefault(field,set()).add(vals)
    return {k:next(iter(v)) for k,v in result.items() if len(v)==1}

def matching_columns(rows, anchors):
    """An exact value tuple identifies one column, never a positional guess."""
    if not anchors or not all(type(v) is int and k in rows for k,v in anchors.items()):
        return []
    return [i for i in range(min(len(rows[k]) for k in anchors))
            if all(rows[k][i]==v for k,v in anchors.items())]

def note_units_are_krw(units):
    return bool(units) and all(unit in {'원', '원,주', '원,주,원/주'}
                               for unit in units)

def note_row_label(text):
    label = re.sub(r'^(?:[0-9]+\.\s*|\([0-9]+\)\s*)', '', text.strip())
    return re.sub(r'\((?:주|주석)[0-9]+\)$', '', norm(label))

def reported_eps_number(text):
    value = norm(text)
    if re.fullmatch(r'-?[0-9,]+\.0+', value):
        value = value.split('.', 1)[0]
    return number(value)

def owner_profit_from_note(rows, expected_eps, diagnostics):
    fields = {
        'profit': {'지배주주당기순이익', '지배기업의소유주에게귀속되는당기순이익',
                   '지배기업소유주귀속당기순이익', '지배주주당기순손익',
                   '지배기업소유주지분순이익'},
        'shares': {'가중평균유통보통주식수', '가중평균유통보통주식수(주)',
                   '가중평균유통보통주식수량'},
        'eps': LABELS['eps'] | {'기본주당이익(원)', '보통주기본주당이익'},
    }
    if type(expected_eps) is not int or expected_eps == 0:
        return {}
    values = {}
    for field, labels in fields.items():
        parse = reported_eps_number if field == 'eps' else number
        matches = {tuple(parse(cell) for cell in row[1:])
                   for row in rows if note_row_label(row[0]) in labels}
        if len(matches) != 1:
            reason = 'absent' if not matches else 'ambiguous'
            diagnostics[f'EPS_NOTE_{field}_row_{reason}'] += 1
        else:
            values[field] = matches.pop()
    if len(values) != len(fields):
        return {}
    columns = matching_columns(values, {'eps': expected_eps})
    if len(columns) != 1:
        diagnostics['EPS_NOTE_saved_eps_not_unique'] += 1
        return {}
    index = columns[0]
    profit, shares = (values[field][index] for field in ('profit', 'shares'))
    if profit is None or shares is None or shares <= 0:
        diagnostics['EPS_NOTE_same_column_numbers_missing'] += 1
        return {}
    if 2 * abs(profit - expected_eps * shares) > shares:
        diagnostics['EPS_NOTE_profit_reconciliation_failed'] += 1
        return {}
    return {'owners_profit': profit}

def facts_for_table(rows, anchors, section, diagnostics):
    if section == 'EPS_NOTE':
        return owner_profit_from_note(rows, anchors.get('reported_eps'), diagnostics)
    vals=extract_rows(rows)
    result={}
    if section == 'CF':
        keys=('cfo',)
        fields=('ppe','cip','intangible','investing')
    elif section == 'IS':
        keys=('revenue','operating_income')
        fields=('owners_profit','eps')
    else:
        keys=('equity','liabilities')
        fields=('owners_equity',)
    cols=matching_columns(vals,{k:anchors.get(k) for k in keys})
    if len(cols)!=1:
        return result
    i=cols[0]
    for key in fields:
        if key in vals and len(vals[key])>i and vals[key][i] is not None:
            n=vals[key][i]
            # Only explicit cash outflows or explicit numeric zero.
            if key in ('ppe','cip','intangible'):
                if n>0:continue
                n=-n
            result[key]=n
    return result

def section_title(s, default_scope=None):
    s=norm(s)
    scope='CFS' if '연결' in s else 'OFS' if '별도' in s else default_scope
    if re.fullmatch(r'(?:[0-9]+\.)?주당이익\((?:연결|별도)\)', s):
        return scope, 'EPS_NOTE'
    for end,section in (('현금흐름표','CF'),('포괄손익계산서','IS'),
                        ('손익계산서','IS'),('재무상태표','BS')):
        if s.endswith(end) and len(s)<50:
            return scope,section
    return None

LEGACY_XML_ENCODING = re.compile(
    rb'''^<\?xml\b[^>]*?\bencoding\s*=\s*(["'])(euc[-_]?kr|cp949|windows-949|ks_c_5601-1987)\1''',
    re.IGNORECASE,
)

XML_ERROR_NAMES = {
    code: name.lower()
    for name, message in vars(xml_errors).items()
    if name.startswith('XML_ERROR_') and (code := xml_errors.codes.get(message)) is not None
}
UNDEFINED_ENTITY = xml_errors.codes[xml_errors.XML_ERROR_UNDEFINED_ENTITY]
INVALID_TOKEN = xml_errors.codes[xml_errors.XML_ERROR_INVALID_TOKEN]
BARE_AMPERSAND = rb'&(?![A-Za-z_:][\w:.-]*;|#[0-9]+;|#x[0-9A-Fa-f]+;)'
INVALID_TEXT_TOKENS = re.compile(
    rb'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<!DOCTYPE[^>]*>|'
    + rb'(?<![0-9.,+\-])[\x0b\x0c]+(?![0-9.,+\-])|' + BARE_AMPERSAND,
    re.DOTALL,
)

def normalize_invalid_text(raw, diagnostics):
    def literal(match):
        token = match[0]
        if token == b'&':
            diagnostics['xml_literal_ampersands_normalized'] += 1
            return b'&amp;'
        if token[0] in (11, 12):
            diagnostics['xml_formatting_controls_normalized'] += 1
            return b' '
        return token
    return INVALID_TEXT_TOKENS.sub(literal, raw)

XML_FORBIDDEN_CHARACTERS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')
XML_SYNTAX_CHARACTERS = {
    '&': 'ampersand', '<': 'less_than', '>': 'greater_than', '=': 'equals',
    '"': 'quote', "'": 'quote', '#': 'hash', ';': 'semicolon', '/': 'slash',
}
XML_DECLARATION = re.compile(
    rb'''^(?:\xef\xbb\xbf)?<\?xml\b[^>]*?\bencoding\s*=\s*(["'])([A-Za-z0-9_.-]+)\1''',
    re.IGNORECASE,
)

def record_invalid_token(raw, error, stage, diagnostics):
    """Keep offsets and character classes only, never text from the archive."""
    prefix = f'xml_{stage}_token'
    line, column = error.position
    diagnostics[f'{prefix}_line'] = line
    diagnostics[f'{prefix}_column'] = column
    declared = XML_DECLARATION.match(raw)
    if declared:
        encoding = declared[2].lower()
        kind = ('utf8' if encoding in (b'utf-8', b'utf8') else
                'utf16' if encoding.startswith(b'utf-16') else 'other')
        diagnostics[f'{prefix}_declared_{kind}'] += 1
    try:
        source = raw.decode('utf-8-sig')
    except UnicodeDecodeError as decode_error:
        diagnostics[f'{prefix}_invalid_utf8_offset'] = decode_error.start
        return
    forbidden = {ord(match[0]) for match in XML_FORBIDDEN_CHARACTERS.finditer(source)}
    for codepoint in sorted(forbidden):
        diagnostics[f'{prefix}_forbidden_u{codepoint:04x}'] += 1
    # XML normalizes CRLF and CR before reporting line and column.
    lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    if not 1 <= line <= len(lines):
        return
    row = lines[line - 1]
    for label, offset in (('at', column), ('before', column - 1)):
        if 0 <= offset < len(row):
            char = row[offset]
            kind = XML_SYNTAX_CHARACTERS.get(char, category(char).lower())
            diagnostics[f'{prefix}_{label}_{kind}'] += 1

def invalid_token_category(raw, position):
    try:
        source = raw.decode('utf-8')
    except UnicodeDecodeError:
        return 'xml_invalid_token_non_utf8_bytes'
    line, column = position
    # XML normalizes CRLF and CR before reporting line and column.
    lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    if 1 <= line <= len(lines):
        text = lines[line - 1]
        if column < len(text) and ord(text[column]) < 32:
            return 'xml_invalid_token_control_character'
        if '&' in text[max(0, column - 1):column + 1]:
            return 'xml_invalid_token_ampersand'
        if '<' in text[max(0, column - 1):column + 1]:
            return 'xml_invalid_token_less_than'
    return 'xml_invalid_token_other_character'
XML_SPACE_TOKENS = re.compile(rb'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|&nbsp;', re.DOTALL)

def xml_root(raw, diagnostics):
    # Expat does not handle legacy multibyte Korean declarations directly.
    # Normalize only the recognized encoding and preserve the exact text.
    match = LEGACY_XML_ENCODING.match(raw)
    if match:
        declared = match[2].lower()
        encoding = 'euc_kr' if declared in (b'euc-kr', b'euc_kr', b'euckr') else 'cp949'
        start, end = match.span(2)
        raw = (raw[:start] + b'UTF-8' + raw[end:]).decode(encoding).encode('utf-8')
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        # Preserve declarations, comments and CDATA. Any repaired input still
        # has to pass the same strict tree parse and financial anchors.
        if b'<!ENTITY' in raw:
            raise
        if error.code == UNDEFINED_ENTITY and b'&nbsp;' in raw:
            diagnostics['xml_nbsp_retry'] += 1
            normalized = XML_SPACE_TOKENS.sub(
                lambda m: b'&#160;' if m[0] == b'&nbsp;' else m[0], raw)
            return ET.fromstring(normalized)
        if error.code == INVALID_TOKEN:
            diagnostics[invalid_token_category(raw, error.position)] += 1
            record_invalid_token(raw, error, 'initial', diagnostics)
            normalized = normalize_invalid_text(raw, diagnostics)
            if normalized != raw:
                diagnostics['xml_invalid_text_retry'] += 1
                try:
                    root = ET.fromstring(normalized)
                except ET.ParseError as retry_error:
                    if retry_error.code == INVALID_TOKEN:
                        record_invalid_token(normalized, retry_error, 'retry', diagnostics)
                    raise
                diagnostics['xml_invalid_text_repaired'] += 1
                return root
        raise

def checked_xml_root(raw, diagnostics):
    # No external entities, remote DTDs or permissive tree recovery.
    try:
        root = xml_root(raw, diagnostics)
    except ET.ParseError as error:
        diagnostics['xml_rejected'] += 1
        diagnostics[XML_ERROR_NAMES.get(error.code, 'xml_error_other')] += 1
        return None
    except (LookupError, ValueError) as error:
        diagnostics['xml_rejected'] += 1
        if isinstance(error, UnicodeError):
            diagnostics['xml_encoding_invalid_bytes'] += 1
        elif isinstance(error, LookupError):
            diagnostics['xml_encoding_unknown'] += 1
        else:
            diagnostics['xml_encoding_unsupported'] += 1
        return None
    diagnostics['xml_parsed'] += 1
    return root

def parse_xml(raw, scope, anchors, *, diagnostics=None):
    if diagnostics is None:
        diagnostics = Counter()
    root = checked_xml_root(raw, diagnostics)
    return {} if root is None else facts_from_root(root, scope, anchors, diagnostics)

def facts_from_root(root, scope, anchors, diagnostics):
    scope_context=None; active=None; unit=False; candidates={}
    for node in root.iter():
        kind=tag(node)
        if kind in ('TITLE','P','SUBTITLE'):
            t=norm(value(node))
            if len(t)>150:continue
            if re.fullmatch(r'(?:[0-9.]+)?연결재무제표',t):
                scope_context='CFS';active=None;unit=False
            elif re.fullmatch(r'(?:[0-9.]+)?(?:별도)?재무제표',t):
                scope_context='OFS';active=None;unit=False
            elif '주석' in t and (kind=='TITLE' or re.match(r'^[0-9.]+',t)):
                active=None;unit=False
            else:
                title=section_title(t,scope_context)
                if title:
                    active=title;unit=False
                    if active[0] == scope:
                        diagnostics[f'{active[1]}_heading'] += 1
                elif kind=='TITLE' or (
                        active and active[1] == 'EPS_NOTE' and re.match(r'^[0-9]+\.', t)):
                    active=None;unit=False
            if active:
                units = declared_units((t,))
                if units:
                    unit = note_units_are_krw(units) if active[1] == 'EPS_NOTE' else units == {'원'}
        elif kind=='TABLE' and active and active[0]==scope:
            rows=table_rows(node)
            # A table's own unit takes precedence over the section header.
            units = declared_units(cell for row in rows[:5] for cell in row)
            diagnostic_key = active[1]
            is_krw = unit
            if units:
                is_krw = note_units_are_krw(units) if diagnostic_key == 'EPS_NOTE' else units == {'원'}
            if not is_krw:
                diagnostics[f'{diagnostic_key}_unit_unconfirmed'] += 1
                continue
            diagnostics[f'{diagnostic_key}_krw_table'] += 1
            f=facts_for_table(rows,anchors,diagnostic_key,diagnostics)
            if f:
                diagnostics[f'{diagnostic_key}_matched_table'] += 1
            for k,v in f.items():candidates.setdefault(k,set()).add(v)
    return {k:next(iter(v)) for k,v in candidates.items() if len(v)==1}

class CompletedNoteReader:
    """Project only fully traversed EPS notes from a strictly parsed prefix."""
    def __init__(self, scope):
        self.scope = scope
        self.builder = ET.TreeBuilder()
        self.table_depth = 0
        self.root = None
        self.pending = None
        self.completed = ET.Element('COMPLETED_NOTES')

    def start(self, name, attrs):
        node = self.builder.start(name, attrs)
        kind = tag(node)
        if self.root is None:
            self.root = kind
        if kind == 'TABLE':
            self.table_depth += 1

    def data(self, text):
        self.builder.data(text)

    def end(self, name):
        node = self.builder.end(name)
        kind = tag(node)
        if kind == 'TABLE':
            self.table_depth -= 1
            if not self.table_depth and self.pending is not None:
                self.pending.append(node)
            return
        if self.table_depth or kind not in ('TITLE','P','SUBTITLE'):
            return
        label = norm(value(node))
        heading = section_title(label)
        if heading or kind == 'TITLE' or re.match(r'^[0-9]+\.', label):
            if self.pending is not None:
                self.completed.append(self.pending)
                self.pending = None
            if heading == (self.scope, 'EPS_NOTE'):
                self.pending = ET.Element('NOTE')
        if self.pending is not None:
            self.pending.append(node)


def candidates_from_xml_prefix(body, scope, anchors, diagnostics):
    if (not diagnostics.get('xml_error_invalid_token')
            or b'<!ENTITY' in body
            or not any(type(anchor.get('reported_eps')) is int for anchor in anchors.values())):
        return {}
    declared = XML_DECLARATION.match(body)
    if declared and declared[2].lower() not in (b'utf-8', b'utf8'):
        return {}
    try:
        body.decode('utf-8-sig')
    except UnicodeDecodeError:
        return {}
    reader = CompletedNoteReader(scope)
    parser = ET.XMLParser(target=reader)
    try:
        parser.feed(normalize_invalid_text(body, Counter()))
    except ET.ParseError:
        pass
    if reader.root != 'DOCUMENT' or not len(reader.completed):
        return {}
    diagnostics['xml_prefix_complete_eps_notes'] = len(reader.completed)
    found = {}
    for year, anchor in anchors.items():
        eps = anchor.get('reported_eps')
        if type(eps) is not int:
            continue
        facts = facts_from_root(reader.completed, scope, {'reported_eps': eps}, diagnostics)
        if 'owners_profit' in facts:
            found[year] = {'owners_profit': facts['owners_profit']}
    if found:
        diagnostics['xml_prefix_candidate_used'] += 1
    return found


def inspect(raw, receipt, scope, anchors, *, diagnostics=None):
    try:
        with ZipFile(BytesIO(raw)) as z:
            infos=z.infolist()
            if (len(infos)>100 or sum(x.file_size for x in infos)>MAX_EXPANDED
                    or any(x.flag_bits & 1 for x in infos)):
                raise PilotError('unsafe_document_archive')
            # Main annual report only; never mix audit attachments/scopes.
            selected=[x for x in infos if Path(x.filename).name.lower()==receipt+'.xml']
            if len(selected)!=1:
                return {},'main_document_not_identified'
            body=z.read(selected[0])
    except BadZipFile:
        raise PilotError('provider_non_zip_response') from None
    if diagnostics is None:
        diagnostics = Counter()
    root = checked_xml_root(body, diagnostics)
    if root is None:
        found = candidates_from_xml_prefix(body, scope, anchors, diagnostics)
    else:
        found = {year:facts_from_root(root,scope,anchor,diagnostics)
                 for year,anchor in anchors.items()}
    found={year:row for year,row in found.items() if row}
    return found, 'anchored_candidates' if found else 'no_anchored_candidate'

def fetch(key, receipt):
    if not re.fullmatch(r'[A-Za-z0-9]{40}',key) or not RECEIPT.fullmatch(receipt):
        raise PilotError('invalid_document_request')
    url='https://opendart.fss.or.kr/api/document.xml?'+urlencode(
        {'crtfc_key':key,'rcept_no':receipt})
    try:
        with urlopen(Request(url,headers={'User-Agent':'v3-financial-document-pilot'}),timeout=45) as r:
            raw=r.read(MAX_ZIP+1)
    except Exception:
        raise PilotError('document_transport_failed') from None
    if len(raw)>MAX_ZIP:
        raise PilotError('document_exceeds_bound')
    return raw

def queue(priority,book,cash,prior,bulk,eps,profit):
    records={r['code']:r for r in book['records']}
    out=[];seen=set()
    singles={r['code']:r for r in priority['single_component_queue']}
    all_gaps={code:(avail,priority['shape_names'][idx].split('+'))
              for code,avail,_,idx,_ in priority['multiple_missing_index']}
    for code,r in singles.items():
        all_gaps[code]=(r['available'],[g['area']+':'+g['component'] for g in r['gaps']])
    codes=sorted(all_gaps,key=lambda code:(code not in SEEDS,
                all_gaps[code][0]<90,len(all_gaps[code][1]),-all_gaps[code][0],code))
    for code in codes:
        avail,gaps=all_gaps[code]
        if not 60<=avail<100 or not any(k.split(':')[-1] in FIN for k in gaps):
            continue
        a=records.get(code,{}).get('annual') or {}
        if a.get('year')!=2025 or a.get('currency')!='KRW' or a.get('fs_div') not in ('CFS','OFS'):
            continue
        yrows=cash['records'].get(code,{}).get('years',{})
        # FY2025 annual report first, except a known unresolved older-year
        # direct source (Jinro). Then other exact annual receipt gaps.
        sources=[]
        vals=a.get('values',{})
        historical=bulk['records'].get(code,{}).get('years',{})
        epsrow=eps['records'].get(code,{})
        epsvalues=epsrow.get('reported_basic_eps_2023_2025') or [None]*3
        if epsrow.get('annual_receipt_id')!=a.get('receipt_id') or epsrow.get('fs_div')!=a['fs_div']:
            epsvalues=[None]*3
        sources.append((2025,a.get('receipt_id'),a['fs_div'],
                        {k:vals.get(v) for k,v in
                         [('cfo','operating_cashflow'),('revenue','revenue'),
                          ('operating_income','operating_income'),('equity','equity'),
                          ('liabilities','liabilities')]}))
        for year,row in sorted(yrows.items()):
            if row.get('free_cashflow_krw') is None and row.get('major_annual_receipt_matched') is True:
                sources.append((int(year),row.get('annual_receipt_id'),row.get('scope'),
                                {'cfo':row.get('operating_cashflow_krw')}))
        if code=='018120':sources.sort(key=lambda x:x[0])
        for year,receipt,scope,anchors in sources:
            if (not isinstance(receipt,str) or not RECEIPT.fullmatch(receipt)
                or receipt[:4]!=str(year+1) or receipt[:8]>priority['asof'].replace('-','')
                or scope not in ('CFS','OFS') or receipt in prior or receipt in seen
                or not any(type(v) is int for v in anchors.values())):continue
            seen.add(receipt)
            comparisons={};wanted={}
            for y in range(2023,year+1):
                ys=str(y);old=yrows.get(ys,{})
                h=historical.get(ys,{}).get(scope,{})
                hv=h.get('values',{})
                if not RECEIPT.fullmatch(str(h.get('receipt_id',''))):continue
                if y==year and h.get('receipt_id')!=receipt:continue
                if (old.get('scope')!=scope or old.get('annual_receipt_id')!=h.get('receipt_id')
                    or old.get('major_annual_receipt_matched') is not True):old={}
                anchor={k:hv.get(k) for k in ('revenue','operating_income','equity','liabilities')}
                anchor['cfo']=old.get('operating_cashflow_krw')
                if y==year:
                    anchor.update({k:v for k,v in anchors.items() if v is not None})
                comparisons[ys]=anchor
                needed=[]
                if old and old.get('free_cashflow_krw') is None:
                    if old.get('ppe_capex_krw') is None:needed.extend(('ppe','cip'))
                    if old.get('intangible_capex_krw') is None:needed.append('intangible')
                if 'valuation:per_eps_quality_growth' in gaps and epsvalues[y-2023] is None:
                    needed.append('eps')
                if y==2025:
                    supplement=profit['records'].get(code,{})
                    if ('valuation:per_eps_quality_growth' in gaps and vals.get('owners_profit') is None
                        and not (supplement.get('annual_receipt_id')==receipt and
                                 type(supplement.get('owners_profit_krw')) is int)):
                        needed.append('owners_profit')
                    if 'valuation:pbr_equity_quality' in gaps and vals.get('owners_equity') is None:
                        needed.append('owners_equity')
                if needed:wanted[ys]=needed
            if not wanted:
                seen.remove(receipt)
                continue
            out.append({'code':code,'year':year,'receipt':receipt,'scope':scope,
                        'anchors':comparisons,'wanted':wanted,'gaps':gaps})
            # Diverse issuer pilot; next invocation selects other receipts.
            break
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=('dry-run','live'),default='dry-run')
    p.add_argument('--limit',type=int,default=LIMIT)
    p.add_argument('--review-owner-note',action='store_true')
    a=p.parse_args()
    if not 1<=a.limit<=LIMIT:raise PilotError('invalid_batch_limit')
    root=Path('docs');read=lambda n:json.loads((root/n).read_text())
    priority=read('v3-operational-evidence-priority.json')
    catalog=read('v22-market-catalog.json')
    book=read('v3-dart-book-reference.json')
    cash=read('v3-dart-cashflow-reference.json')
    bulk=read('v3-dart-bulk-history.json')
    eps=read('v3-dart-reported-eps.json')
    profit=read('v3-dart-owner-profit-supplement.json')
    report=read('v3-operational-research-scores.json')
    comparison=read('v3-operational-research-comparison.json')
    from v3_v30b_release_gate import r1d_public_reconciles
    if (priority['asof']!=catalog['date'] or book['asof']!=catalog['date']
        or cash['asof']!=catalog['date'] or bulk['asof']!=catalog['date'] or priority['listed_total']!=catalog['listed_total']
        or report['asof']!=catalog['date'] or r1d_public_reconciles(report,comparison,catalog) is None):
        raise PilotError('unaligned_pilot_sources')
    target=root/OUT
    if target.is_symlink():raise PilotError('unsafe_output')
    old=read(OUT) if target.exists() else {'schema':SCHEMA,'records':{},'scores_unchanged':True}
    if old.get('schema')!=SCHEMA or old.get('scores_unchanged') is not True or type(old.get('records')) is not dict:
        raise PilotError('invalid_pilot_checkpoint')
    records=old['records']
    for receipt,r in records.items():
        if (not RECEIPT.fullmatch(receipt) or r.get('receipt')!=receipt
            or not re.fullmatch('[a-f0-9]{64}',str(r.get('archive_sha256')))
            or r.get('status') not in ('main_document_not_identified','anchored_candidates','no_anchored_candidate')):
            raise PilotError('invalid_pilot_receipt')
    if a.review_owner_note:
        previous = records.get(NOTE_RECEIPT, {})
        q = []
        if previous.get('status') == 'no_anchored_candidate' and previous.get('reader') != NOTE_READER:
            for item in queue(priority,book,cash,{},bulk,eps,profit):
                if item['code'] != '210540' or item['receipt'] != NOTE_RECEIPT:
                    continue
                eps_record = eps['records'].get(item['code'], {})
                series = eps_record.get('reported_basic_eps_2023_2025')
                if (item['scope'] == 'CFS' and item['wanted'].get('2025') == ['owners_profit']
                        and eps_record.get('annual_receipt_id') == NOTE_RECEIPT
                        and eps_record.get('fs_div') == 'CFS'
                        and isinstance(series, list) and len(series) == 3
                        and all(type(value) in (int, float) and abs(value) <= 10**9
                                and value % 1 == 0 for value in series)
                        and series.count(series[-1]) == 1 and series[-1] != 0):
                    item['anchors']['2025']['reported_eps'] = int(series[-1])
                    q.append(item)
                break
    else:
        q=queue(priority,book,cash,records,bulk,eps,profit)[:a.limit]
    if any(x['code'] not in report['rows'] or not 60<=report['rows'][x['code']]['available']<100 for x in q):
        raise PilotError('unverified_pilot_selection')
    summary={'asof':catalog['date'],'selected':len(q),'provider_calls':0,'completed':0,
             'candidate_facts':0,'candidate_issuers':0,'scores_unchanged':True,'reasons':{}}
    if a.mode=='dry-run':
        print(json.dumps(summary));return
    key=os.getenv('DART_API_KEY','');reasons=Counter()
    for x in q:
        raw=fetch(key,x['receipt']);summary['provider_calls']+=1
        diagnostics = Counter()
        found,status=inspect(raw,x['receipt'],x['scope'],x['anchors'],diagnostics=diagnostics)
        found={year:{k:v for k,v in row.items() if k in x['wanted'].get(year,[])}
               for year,row in found.items()}
        found={year:row for year,row in found.items() if row}
        if status=='anchored_candidates' and not found:status='no_anchored_candidate'
        records[x['receipt']]={**x,'archive_sha256':sha256(raw).hexdigest(),
                               'status':status,'candidates':found}
        records[x['receipt']]['diagnostics'] = dict(diagnostics)
        if a.review_owner_note:
            records[x['receipt']]['reader'] = NOTE_READER
            summary['target_diagnostics'] = dict(diagnostics)
        summary['completed']+=1;summary['candidate_facts']+=sum(len(row) for row in found.values())
        summary['candidate_issuers']+=bool(found);reasons[status]+=1
        summary['reasons']=dict(reasons)
        from v3_dart_bulk_history import write_json
        write_json(target,{'schema':SCHEMA,'asof':catalog['date'],'records':records,
                           'scores_unchanged':True,'raw_documents_published':False})
    print(json.dumps(summary,sort_keys=True))

if __name__=='__main__':main()
