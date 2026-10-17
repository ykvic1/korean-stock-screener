"""Approved KRX/DART API adapters for bounded, non-publishing probes.

All exceptions are deliberately sanitized: DART sends the credential as a query
parameter, and library exceptions may contain the full URL. Never log that URL.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
import json
import re
from xml.etree import ElementTree
from zipfile import ZipFile, BadZipFile

import requests

KRX_BASE = 'https://data-dbg.krx.co.kr/svc/apis/'
DART_BASE = 'https://opendart.fss.or.kr/api/'
KRX_ROUTES = {'KOSPI': 'sto/stk_bydd_trd', 'KOSDAQ': 'sto/ksq_bydd_trd'}
MAX_KRX_BODY = 20_000_000
MAX_DART_ZIP = 25_000_000
MAX_DART_XML = 65_000_000


class ProbeError(Exception):
    """Public-safe diagnostic that never includes credentials or URL parameters."""


def canonical_date(asof: str) -> str:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', asof):
        raise ValueError('Date must be YYYY-MM-DD')
    date.fromisoformat(asof)
    return asof.replace('-', '')


def code6(code: str) -> str:
    code = str(code)
    if not re.fullmatch(r'\d{6}', code):
        raise ValueError('Stock code must be exactly six digits')
    return code


def _get(session, url, *, headers=None, params=None, limit=MAX_KRX_BODY):
    try:
        # Prevent an unexpected redirect from forwarding a DART credential in URL.
        response = session.get(url, headers=headers, params=params, timeout=25,
                               allow_redirects=False)
        if response.status_code != 200:
            raise ProbeError('Official API HTTP response is not 200 (authorization/service may be pending)')
        payload = response.content
        if len(payload) == 0 or len(payload) > limit:
            raise ProbeError('Official API response is empty or larger than probe limit')
        return payload
    except ProbeError:
        raise
    except Exception:
        # Do not include exception text: requests may embed crtfc_key in it.
        raise ProbeError('Official API transport failed (request details suppressed)') from None


def _json(payload: bytes) -> dict:
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError):
        raise ProbeError('Official API did not return valid JSON') from None
    if not isinstance(data, dict):
        raise ProbeError('Official API returned unexpected JSON root')
    return data


class KRXClient:
    """KRX published stock daily endpoint; one request per market per date."""

    def __init__(self, key: str, *, session=None):
        if not isinstance(key, str) or not key.strip():
            raise ValueError('KRX_API_KEY is required')
        self._key = key.strip()
        self.session = session or requests.Session()

    def daily_market(self, market: str, asof: str) -> tuple[list[dict], bytes]:
        if market not in KRX_ROUTES:
            raise ValueError('KRX market must be KOSPI or KOSDAQ')
        day = canonical_date(asof)
        payload = _get(self.session, KRX_BASE + KRX_ROUTES[market],
                       headers={'AUTH_KEY': self._key}, params={'basDd': day})
        parsed = _json(payload)
        rows = parsed.get('OutBlock_1')
        if not isinstance(rows, list):
            raise ProbeError('KRX response missing OutBlock_1 stock list')
        if not rows:
            raise ProbeError('KRX returned zero rows; holiday, pending data or API issue')
        if any(not isinstance(row, dict) for row in rows):
            raise ProbeError('KRX response contains non-object rows')
        return rows, payload


class DARTClient:
    """No report calculations/PIT claims; sample API accessibility only."""

    def __init__(self, key: str, *, session=None):
        if not isinstance(key, str) or not key.strip():
            raise ValueError('DART_API_KEY is required')
        self._key = key.strip()
        self.session = session or requests.Session()

    def _api(self, endpoint: str, **params) -> dict:
        if endpoint not in ('list.json', 'fnlttSinglAcntAll.json', 'company.json'):
            raise ValueError('DART endpoint not in fixed probe allowlist')
        payload = _get(self.session, DART_BASE + endpoint,
                       params={'crtfc_key': self._key, **params})
        result = _json(payload)
        status = str(result.get('status', ''))
        if status == '013':
            return {'status': '013', 'list': []}
        if status != '000':
            # Never echo vendor message: may include submitted parameters.
            raise ProbeError('OpenDART status ' + (status if re.fullmatch(r'\d{3}', status) else 'unknown'))
        return result

    def original_document_zip(self, receipt: str) -> bytes:
        """Fetch one disclosure ZIP by receipt. Never expose URLs or credential."""
        if not re.fullmatch(r'\d{14}', str(receipt)):
            raise ValueError('Document receipt must be exactly 14 digits')
        return _get(self.session, DART_BASE + 'document.xml',
                    params={'crtfc_key': self._key, 'rcept_no': receipt},
                    limit=MAX_DART_ZIP)

    def corp_mapping(self) -> dict[str, str]:
        raw = _get(self.session, DART_BASE + 'corpCode.xml',
                   params={'crtfc_key': self._key}, limit=MAX_DART_ZIP)
        try:
            with ZipFile(BytesIO(raw)) as z:
                entries = [i for i in z.infolist() if i.filename.upper() == 'CORPCODE.XML']
                if len(entries) != 1 or entries[0].file_size > MAX_DART_XML:
                    raise ProbeError('DART mapping file missing or too large')
                xml = z.read(entries[0])
            root = ElementTree.fromstring(xml)
        except (BadZipFile, KeyError, ElementTree.ParseError, OSError, RuntimeError):
            raise ProbeError('DART company mapping ZIP/XML is invalid') from None
        mapping: dict[str, str] = {}
        for item in root.findall('list'):
            stock = (item.findtext('stock_code') or '').strip()
            corp = (item.findtext('corp_code') or '').strip()
            if re.fullmatch(r'\d{6}', stock) and re.fullmatch(r'\d{8}', corp):
                if stock in mapping and mapping[stock] != corp:
                    raise ProbeError('DART stock code maps to multiple companies')
                mapping[stock] = corp
        if not mapping:
            raise ProbeError('DART mapping has no usable listed stock codes')
        return mapping

    def recent_filings(self, corp_code: str, start: str, end: str) -> list[dict]:
        if not re.fullmatch(r'\d{8}', str(corp_code)):
            raise ValueError('corp_code must be 8 digits; not a stock code')
        start_dd, end_dd = canonical_date(start), canonical_date(end)
        if start_dd > end_dd:
            raise ValueError('Start cannot be after end')
        response = self._api('list.json', corp_code=corp_code, bgn_de=start_dd,
                             end_de=end_dd, page_no=1, page_count=100)
        if response.get('status') == '013':
            return []
        items = response.get('list')
        if not isinstance(items, list):
            raise ProbeError('DART filings response lacks list')
        return items

    def annual_statement_sample(self, corp_code: str, business_year: int,
                                fs_div: str = 'CFS') -> dict:
        if not re.fullmatch(r'\d{8}', str(corp_code)):
            raise ValueError('corp_code must be 8 digits')
        if fs_div not in ('CFS', 'OFS') or not (2015 <= business_year <= 2100):
            raise ValueError('Invalid DART statement parameters')
        data = self._api('fnlttSinglAcntAll.json', corp_code=corp_code,
                         bsns_year=str(business_year), reprt_code='11011', fs_div=fs_div)
        rows = data.get('list', [])
        if not isinstance(rows, list):
            raise ProbeError('DART financial statement response lacks list')
        # Does not claim disclosure as-of eligibility from report year alone.
        return {'rows': len(rows), 'status': 'observed_unverified_disclosure_time' if rows else 'no_data',
                'asof_join_validated': False, 'statement_type': fs_div,
                'business_year': business_year, 'metric_values': None}
