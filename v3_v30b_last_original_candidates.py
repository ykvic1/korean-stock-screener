"""Recover only the residual original-filing candidates in the frozen ledger."""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

from v3_dart_original_revenue_recovery import choices, verified, OUTPUT
import v3_dart_original_revenue_recovery as revenue
import v3_dart_xbrl_cashflow_recovery as fcf


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixed-root", type=Path, required=True)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--live", action="store_true")
    a=p.parse_args()
    root=a.fixed_root.resolve()
    ledger=json.loads(a.ledger.read_text())
    if ledger["asof"]!="2026-11-20" or ledger["listed_total"]!=2592 or len(ledger["rows"])!=2592:
        raise ValueError("invalid_frozen_ledger")
    def read(name):
        return json.loads((root/"docs"/name).read_text())
    catalog=read("v22-market-catalog.json")
    report=read("v3-operational-research-scores.json")
    if catalog["date"]!=ledger["asof"] or report["source_digest"]!=ledger["source_digest"]:
        raise ValueError("stale_frozen_ledger")
    def pending(code,area,key):
        return ledger["rows"][code]["parts"][area][key]["source_review_pending"]
    revenue_codes={code for code in ledger["rows"] if any(pending(code,a,k) for a,k in (
        ("financial","revenue_growth"),("financial","operating_growth_margin"),("valuation","sales_to_cap")))}
    cash_codes={code for code in ledger["rows"] if any(pending(code,a,k) for a,k in (
        ("financial","cashflow_fcf_persistence"),("valuation","fcf_yield")))}
    expected=choices(catalog,read("v3-dart-bulk-history.json"))
    retained=verified(read(OUTPUT),expected,catalog["date"])
    def selected_revenue(_report, expected, retained):
        return sorted(k for k,v in expected.items() if k not in retained and v["code"] in revenue_codes
          and (pending(v["code"],"financial","revenue_growth")
            or (v["fiscal_year"]>=2023 and pending(v["code"],"financial","operating_growth_margin"))
            or (v["fiscal_year"]==2025 and pending(v["code"],"valuation","sales_to_cap"))))[:revenue.LIMIT]
    selected=selected_revenue(report,expected,retained)
    old_targets=fcf.targets
    def selected_fcf(state,priority,limit,*args,**kwargs):
        filtered={**state,"records":{c:r for c,r in state["records"].items() if c in cash_codes}}
        return old_targets(filtered,priority,limit,*args,**kwargs)
    print(json.dumps({"pending_issuers":ledger["pending_source_review_issuers"],
         "pending_revenue_issuers":len(revenue_codes),"pending_cashflow_issuers":len(cash_codes),
         "original_revenue_receipts_selected":len(selected),"live":a.live}))
    if not a.live:
        return
    os.chdir(root)
    if selected:
        with patch.object(revenue,"targets",selected_revenue):
            revenue.main()
    # Reuse the existing validated, bounded original-XBRL implementation.
    # Only unresolved ledger issuers are considered, not the 260 optional candidates.
    if cash_codes:
        import sys
        with patch.object(fcf,"targets",selected_fcf), patch.object(sys,"argv",["fcf","--mode","live","--limit","32"]):
            fcf.main()


if __name__=="__main__":
    main()
