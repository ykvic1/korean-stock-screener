"""Exact 2025 five-firm research mapping observed in two independent DART runs.

Values are historical observations, not live authoritative mapping proof.
The pinned mapping is usable ONLY for the fixed five-company OFS research cohort.
Annual filing and financial API company/receipt identity checks remain mandatory.
"""
from __future__ import annotations

PINNED_ASOF = '2026-10-16'
PINNED_CASES = {
    '252990': ('01199727', '20260316001120', 'OFS'),
    '280360': ('01258507', '20260312000907', 'OFS'),
    '018880': ('00161125', '20260318000645', 'OFS'),
    '014680': ('00140955', '20260318001424', 'OFS'),
    '064290': ('00479787', '20260318001550', 'OFS'),
}
SOURCE_RUNS = (39010207507, 39010887836)


def fixed_pilot_corp_mapping(asof: str, requested_cases: dict) -> dict:
    """Reject altered code, order, year, receipt, and accounting basis."""
    if asof != PINNED_ASOF or list(requested_cases) != list(PINNED_CASES):
        raise ValueError('Pinned DART mapping is restricted to the reviewed five-company 2025 cohort')
    for stock, (corp, receipt, basis) in PINNED_CASES.items():
        if requested_cases.get(stock) != (receipt, basis):
            raise ValueError('Pinned DART mapping receipt/basis does not match reviewed cohort')
    return {stock: corp for stock, (corp, _, _) in PINNED_CASES.items()}
