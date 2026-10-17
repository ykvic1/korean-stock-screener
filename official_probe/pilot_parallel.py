"""Bounded two-worker XBRL retrieval for the exact, previously-reviewed 2025 cohort.

Research only. Every worker owns an independent DART client/session.
No retries, no speculative receipts, no PIT or accounting-scope certification.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import perf_counter, sleep

from .clients import ProbeError
from .pinned_2025_pilot import fixed_pilot_corp_mapping

MAX_RESEARCH_WORKERS = 2
REQUEST_START_STAGGER_SECONDS = 0.75


def collect_pinned_pilot(*, key, asof, cases, client_factory, compare,
                         workers=MAX_RESEARCH_WORKERS,
                         start_stagger_seconds=REQUEST_START_STAGGER_SECONDS):
    """Collect five reports; return same ordered company schema as serial mode.

    Concurrency means at most two company sessions simultaneously. Each company
    makes its original bounded filing-list, financial-JSON and XBRL-ZIP calls
    strictly in sequence. Exceptions become an explicit unsuccessful result.
    """
    mapping = fixed_pilot_corp_mapping(asof, cases)
    if type(workers) is not int or not 1 <= workers <= MAX_RESEARCH_WORKERS:
        raise ValueError('Research worker count must be 1 or 2')
    if (not isinstance(start_stagger_seconds, (int, float))
            or isinstance(start_stagger_seconds, bool)
            or not 0 <= start_stagger_seconds <= 2):
        raise ValueError('Research start staggering outside bounded range')

    def one_company(index, code):
        corp = mapping[code]
        receipt, scope = cases[code]
        if start_stagger_seconds:
            sleep(index * start_stagger_seconds)
        started = perf_counter()
        item = {
            'code': code, 'corp_code': corp,
            'status': 'observed_not_pit_validated',
            'financial_metrics_verified': False,
        }
        client = None
        try:
            # Each worker creates its OWN DARTClient and requests.Session.
            client = client_factory(key)
            client.set_research_timeout(12)
            item['xbrl_comparison'] = compare(
                client, corp, int(asof[:4]) - 1, asof, receipt,
                fs_div=scope)
        except (ProbeError, ValueError):
            item['xbrl_comparison'] = {
                'status': 'failed_or_unavailable', 'metrics': None,
                'historical_numeric_snapshot_verified': False,
                'xbrl_accounting_scope_verified': False,
                'confirmed_scoring_allowed': False,
                'investment_data_ready': False,
                'error': 'XBRL comparison unavailable; request details hidden'}
        except Exception:
            # Do not echo requests exception URLs, credentials or ZIP/XML data.
            item['xbrl_comparison'] = {
                'status': 'unexpected_research_error', 'metrics': None,
                'historical_numeric_snapshot_verified': False,
                'xbrl_accounting_scope_verified': False,
                'confirmed_scoring_allowed': False,
                'investment_data_ready': False,
                'error': 'Unexpected research failure; raw details suppressed'}
        finally:
            events = list(getattr(client, 'research_request_timings', []))
            elapsed_ms = int((perf_counter() - started) * 1000)
            item['xbrl_research_timing'] = {
                'company_elapsed_ms': elapsed_ms,
                'sanitized_requests': events,
                'non_network_elapsed_ms': max(
                    0, elapsed_ms - sum(t['elapsed_ms'] for t in events)),
                'network_requests_count': len(events),
            }
            # Always release this company's independent HTTP connection pool.
            session = getattr(client, 'session', None)
            if session is not None and hasattr(session, 'close'):
                try:
                    session.close()
                except Exception:
                    pass
        return item

    start = perf_counter()
    ordered_codes = tuple(cases)
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix='dart-xbrl-pilot') as executor:
        futures = [executor.submit(one_company, i, code)
                   for i, code in enumerate(ordered_codes)]
        results = [f.result() for f in futures]
    return results, {
        'research_workers': workers,
        'research_start_stagger_ms': int(start_stagger_seconds * 1000),
        'research_parallel_wall_ms': int((perf_counter() - start) * 1000),
        'research_parallel_mode': 'bounded_independent_client_per_company_no_retry',
    }
