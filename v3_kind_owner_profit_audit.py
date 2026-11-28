"""Cross-check a reviewed KIND filing against same-period saved DART accounts."""
from collections import Counter
from hashlib import sha256
from json import dumps, loads
from pathlib import Path
import re
from urllib.request import Request, urlopen

from v3_dart_whole_report_pilot import norm, number, owner_profit_from_note

CODE = '210540'
RECEIPT = '20260316001465'
URL = 'https://kind.krx.co.kr/external/2026/03/16/002374/20260316006781/11011.htm'
MAX_BYTES = 8_000_000
SOURCE_ACCOUNT = 'kind:reviewed_consolidated_eps_note'
REVIEWED_PROFIT = 25_143_819_961
REVIEWED_ANCHORS = {
    'revenue': 382_750_873_929, 'operating_income': 30_429_405_841,
    'cfo': 23_102_288_824, 'reported_eps': 2_278,
}


def fetch():
    with urlopen(Request(URL, headers={'User-Agent': 'Mozilla/5.0'}), timeout=25) as response:
        if response.geturl() != URL:
            raise ValueError('Unexpected KIND source URL')
        body = response.read(MAX_BYTES + 1)
    if not 0 < len(body) <= MAX_BYTES:
        raise ValueError('Unexpected KIND document size')
    return body


def audit(body, anchors, expected_eps):
    from bs4 import BeautifulSoup, UnicodeDammit

    decoded = UnicodeDammit(body, is_html=True).unicode_markup
    if decoded is None:
        raise ValueError('KIND report decoding failed')
    doc = BeautifulSoup(decoded, 'html.parser')
    contents = norm(doc.get_text())
    for marker in ('디와이파워', '2025.01.01', '2025.12.31',
                   '연결포괄손익계산서', '연결현금흐름표', '주당이익(연결)'):
        if marker not in contents:
            raise ValueError('KIND filing identity, period or scope could not be confirmed')
    income_profits = set()
    note_profits = set()
    cashflow_found = False
    for table in doc.find_all('table'):
        rows = [[cell.get_text(strip=True) for cell in row.find_all(('td', 'th'), recursive=False)]
                for row in table.find_all('tr') if row.find_parent('table') is table]
        rows = [row for row in rows if row]
        if not rows:
            continue
        current_amounts = {}
        for row in rows:
            if len(row) < 2:
                continue
            label = re.sub(r'^[Ⅰ-ⅫIVX]+[.．]', '', norm(row[0]))
            current_amounts.setdefault(label, set()).add(number(row[1]))
        if (current_amounts.get('수익(매출액)') == {anchors['revenue']}
                and current_amounts.get('영업이익(손실)') == {anchors['operating_income']}):
            income_profits.update(current_amounts.get('당기순이익(손실)', ()))
        if current_amounts.get('영업활동현금흐름') == {anchors['cfo']}:
            cashflow_found = True
        note = owner_profit_from_note(rows, expected_eps, Counter())
        if 'owners_profit' in note:
            note_profits.add(note['owners_profit'])
    if not cashflow_found or len(note_profits) != 1 or note_profits != income_profits:
        raise ValueError('KIND profit, saved EPS and DART account cross-check did not reconcile')
    return note_profits.pop()


def saved_anchors(docs):
    checkpoint = loads((docs / 'v3-dart-whole-report-candidates.json').read_text(encoding='utf-8'))
    record = checkpoint['records'][RECEIPT]
    cash = loads((docs / 'v3-dart-cashflow-reference.json').read_text(encoding='utf-8'))
    annual_cash = cash['records'][CODE]['years']['2025']
    eps = loads((docs / 'v3-dart-reported-eps.json').read_text(encoding='utf-8'))['records'][CODE]
    anchors = record['anchors']['2025']
    expected_eps = anchors['reported_eps']
    if (record['code'] != CODE or record['receipt'] != RECEIPT or record['scope'] != 'CFS'
            or annual_cash['annual_receipt_id'] != RECEIPT or annual_cash['scope'] != 'CFS'
            or annual_cash['major_annual_receipt_matched'] is not True
            or annual_cash['operating_cashflow_krw'] != anchors['cfo']
            or eps['annual_receipt_id'] != RECEIPT or eps['fs_div'] != 'CFS'
            or eps['reported_basic_eps_2023_2025'][-1] != expected_eps):
        raise ValueError('Saved DART filing evidence did not reconcile')
    return anchors


def verify_reviewed(record):
    return (
        record['code'] == CODE and record['annual_receipt_id'] == RECEIPT
        and record['fs_div'] == 'CFS' and record['market'] == 'KOSPI'
        and record['owners_profit_krw'] == REVIEWED_PROFIT
        and record.get('kind_source_url') == URL)


def admit_reviewed(docs, day, expected, retained):
    """Admit the reviewed public fact only against the unchanged DART filing."""
    base = expected.get(CODE)
    old = retained.get(CODE, {})
    if (not base or base['annual_receipt_id'] != RECEIPT or base['fs_div'] != 'CFS'
            or base['market'] != 'KOSPI' or old.get('status') != 'missing_standard_account'):
        return
    try:
        anchors = saved_anchors(docs)
        if any(type(anchors.get(key)) is not int or anchors[key] != value
               for key, value in REVIEWED_ANCHORS.items()):
            return
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return
    recovered = {
        **base, 'observed_on': day, 'raw_provider_rows_published': False,
        'status': 'observed_owner_profit', 'owners_profit_krw': REVIEWED_PROFIT,
        'source_account_id': SOURCE_ACCOUNT, 'kind_source_url': URL,
    }
    if 'original_xbrl_review' in old:
        recovered['original_xbrl_review'] = old['original_xbrl_review']
    retained[CODE] = recovered


def main():
    anchors = saved_anchors(Path('docs'))
    expected_eps = anchors['reported_eps']
    body = fetch()
    profit = audit(body, anchors, expected_eps)
    print(dumps({'candidate_facts': 1, 'owners_profit_krw': profit,
                 'source_sha256': sha256(body).hexdigest(), 'source_url': URL,
                 'dart_annual_receipt': RECEIPT, 'fiscal_year': 2025, 'scope': 'CFS',
                 'scores_unchanged': True}, sort_keys=True))


if __name__ == '__main__':
    main()
