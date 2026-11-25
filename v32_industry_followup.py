"""Select existing saved-source industry jobs only when their inputs changed.
No network, no new schedule, no price/score changes.
"""
import argparse
import json
from pathlib import Path

from v3_dart_bulk_history import _stable_digest
from v3_industry_price_rotation_research import source_ready
from v3_industry_earnings_gap_audit import complete_saved_gap_source_ready
from v3_industry_dual_evidence_watch import build as dual_build, CrossSourceError

PRICE = "v3-industry-price-rotation.yml"
EARNINGS = "v3-industry-earnings-gap.yml"
DUAL = "v3-industry-dual-evidence-watch.yml"


def pending_jobs(stage, read):
    """Use existing gates and full source hashes; unknown inputs defer."""
    if stage not in ("classification", "price", "earnings"):
        raise ValueError("unknown industry followup stage")
    market = read("data.json")
    catalog = read("v22-market-catalog.json")
    industry = read("v3-dart-industry-classification.json")
    if not source_ready(market, catalog, industry):
        return []
    day = catalog["date"]
    hashes = {"catalog": _stable_digest(catalog),
              "market": _stable_digest(market),
              "current_dart_company_industry_codes": _stable_digest(industry)}
    prices = read("v3-industry-observed-relative-strength.json")
    earnings = read("v3-industry-saved-profit-gap-summary.json")
    price_ready = (isinstance(prices, dict) and prices.get("asof") == day
                   and prices.get("source_sha256") == hashes)
    jobs = []
    if stage in ("classification", "earnings") and not price_ready:
        jobs.append(PRICE)
    if stage == "classification":
        bulk = read("v3-dart-bulk-history.json")
        if complete_saved_gap_source_ready(market, catalog, industry, bulk):
            expected = {"market_catalog": hashes["catalog"],
                        "industry_current_company_codes": hashes["current_dart_company_industry_codes"],
                        "dart_annual_receipt_history": _stable_digest(bulk)}
            if not isinstance(earnings, dict) or earnings.get("asof") != day or earnings.get("source_sha256") != expected:
                jobs.append(EARNINGS)
        return jobs
    if not price_ready:
        return jobs
    try:
        candidate = dual_build(earnings, prices)
    except CrossSourceError:
        return jobs
    if candidate != read("v3-industry-dual-evidence-watch.json"):
        jobs.append(DUAL)
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("classification", "price", "earnings"))
    parser.add_argument("--docs", default="docs")
    args = parser.parse_args()
    root = Path(args.docs)
    def read(name):
        try:
            return json.loads((root / name).read_text(encoding="utf8"))
        except FileNotFoundError:
            return None
    for name in pending_jobs(args.stage, read):
        print(name)


if __name__ == "__main__":
    main()
