"""Bound live flow requests without converting missing observations to zero."""
import time


class FlowCollector:
    def __init__(self, primary, fallback, budget_seconds=1200, timeout_limit=3,
                 clock=time.monotonic):
        self.primary, self.fallback = primary, fallback
        self.clock, self.started = clock, clock()
        self.budget_seconds, self.timeout_limit = budget_seconds, timeout_limit
        self.consecutive_timeouts = 0
        self.attempted = 0

    def collect(self, code):
        if self.consecutive_timeouts >= self.timeout_limit:
            return None, 'deferred: consecutive supplier timeouts'
        if self.clock() - self.started >= self.budget_seconds:
            return None, 'deferred: flow collection time budget exhausted'
        self.attempted += 1
        errors = []
        # A failed fallback is never submitted a second time for the same stock.
        for source, fetch in (('KRX', self.primary), ('Npay', self.fallback)):
            if fetch is None:
                continue
            try:
                result = fetch(code)
                self.consecutive_timeouts = 0
                return result, None
            except Exception as exc:
                errors.append(f'{source}: {type(exc).__name__}: {str(exc)[:180]}')
                if source == 'Npay':
                    # Playwright and requests use different timeout subclasses.
                    timed_out = any('timeout' in c.__name__.lower()
                                    for c in type(exc).__mro__)
                    self.consecutive_timeouts = self.consecutive_timeouts + 1 if timed_out else 0
        return None, ' / '.join(errors)

import re
from datetime import datetime
from bs4 import BeautifulSoup

def parse_naver_flow_table(html):
    soup=BeautifulSoup(html,'html.parser');rows={}
    for table in soup.find_all('table'):
        headers=[th.get_text('',strip=True) for th in table.find_all('th')]
        # New Npay table: use net investor shares, never foreign holdings.
        if '외국인' not in headers or '기관' not in headers or '날짜' not in headers:continue
        fi=headers.index('외국인');ii=headers.index('기관');di=headers.index('날짜')
        for tr in table.find_all('tr'):
            cells=tr.find_all('td',recursive=False)
            if len(cells)!=len(headers):continue
            raw=re.sub(r'\s+','',cells[di].get_text())
            if not re.fullmatch(r'(?:\d{2}|\d{4})\.\d{2}\.\d{2}\.?',raw):continue
            date=raw.rstrip('.').replace('.','-')
            if len(date)==8:date='20'+date
            try:datetime.strptime(date,'%Y-%m-%d')
            except ValueError:continue
            def shares(cell):
                value=re.sub(r'\s+','',cell.get_text()).replace(',','').replace('주','').replace('−','-')
                if not re.fullmatch(r'[+-]?\d+',value):raise ValueError('수급 결측값')
                return int(value)
            try:rows[date]=(shares(cells[ii]),shares(cells[fi]))
            except ValueError:continue
    return rows


