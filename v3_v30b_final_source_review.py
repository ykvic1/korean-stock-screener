"""Bounded fixed-date source review: separate recoverable originals from terminal score gaps."""
from collections import Counter
from v3_v30b_release_gate import R1D_PART_LIMITS
from v3_operational_evidence_priority import category, SHORT_PRICE_HISTORY

CUTOFF = "2026-11-20"


def review_remaining(report, catalog, read, existing):
    from v3_dart_bulk_history import plan as bulk_plan
    from v3_dart_reported_eps import plan as eps_plan
    from v3_dart_revenue_2022 import plan as revenue22
    from v3_dart_revenue_2023_comparative import plan as revenue23
    from v3_dart_revenue_2024_comparative import plan as revenue24
    from v3_dart_operating_income_comparative import plan as operating
    from v3_dart_original_revenue_recovery import choices, verified, OUTPUT
    from v3_dart_xbrl_eps_recovery import recovery_candidates
    from v3_dart_xbrl_cashflow_recovery import targets as fcf_targets
    from v3_dart_cashflow_enrichment import retained_current_market_cashflow, verify_state
    from v3_regular_close_contract import admitted_rows

    day = catalog["date"]
    if day != CUTOFF or report["asof"] != day:
        raise ValueError("fixed_review_date")
    market = read("data.json")
    history = read("v3-dart-bulk-history.json")
    book = read("v3-dart-book-reference.json")
    eps = read("v3-dart-reported-eps.json")
    direct = read("v3-dart-revenue-2022-supplement.json")
    gaps = {code:{(area,key) for area, limits in R1D_PART_LIMITS.items()
                     for key, maximum in limits.items()
                     if maximum - row["parts"][area].get(key,[0,0])[1] > .00001}
            for code,row in report["rows"].items()}
    _,_,_,_,bulk_queue = bulk_plan(market,catalog,prior=history,recover_seed_balance=True)
    _,_,_,retained_eps,eps_queue = eps_plan(catalog,book,eps,recover_missing_names=True)
    plans = {
        "bulk": sum(map(len,bulk_queue)),
        "eps": len(eps_queue),
        "revenue22": len(revenue22(catalog,history,direct)[-1]),
        "revenue23": len(revenue23(catalog,history,direct,read("v3-dart-revenue-2023-comparative.json"))[-1]),
        "revenue24": len(revenue24(catalog,history,read("v3-dart-revenue-2024-comparative.json"),
                          source_gap_codes={code for code,g in gaps.items() if ("financial","revenue_growth") in g})[-1]),
        "operating": len(operating(catalog,history,read("v3-dart-operating-income-2025-comparative.json"))[-1]),
    }
    cash = retained_current_market_cashflow(market,catalog,history,read("v3-dart-cashflow-reference.json"))
    verify_state(cash,market,catalog,history,strict_fingerprint=True)
    expected = choices(catalog,history)
    retained = verified(read(OUTPUT),expected,day)
    pending_revenue = {code for key,base in expected.items()
        if key not in retained
        for code in [base["code"]]
        if gaps[code] & ({("financial","revenue_growth")}
            | ({("financial","operating_growth_margin")} if base["fiscal_year"]>=2023 else set())
            | ({("valuation","sales_to_cap")} if base["fiscal_year"]==2025 else set()))}
    pending_eps = {code for code,_,_ in recovery_candidates(retained_eps,cash,history)
                   if ("valuation","per_eps_quality_growth") in gaps[code]}
    # Existing account audits are terminal after attempted missing accounts;
    # identify original-file candidates separately for the residual queue.
    pending_fcf = {code for code,issuer in cash["records"].items()
                   if gaps[code] & {("financial","cashflow_fcf_persistence"),("valuation","fcf_yield")}
                   and any(row.get("free_cashflow_krw") is None
                           and row.get("operating_cashflow_krw") is not None
                           and row.get("major_annual_receipt_matched") is True
                           and row.get("original_xbrl_review") is None
                           for row in issuer["years"].values())}
    progress = read("v3-regular-close-history-progress.json")
    source = read("v3-regular-close-indicators.json")
    close_complete = (progress.get("asof")==day and progress.get("listed_total")==report["listed_total"]
        and progress.get("pending_sessions")==0
        and progress.get("requested_sessions")==progress.get("archived_sessions")
        and progress.get("requested_completed_weeks")==progress.get("archived_completed_weeks")
        and source.get("asof")==day
        and source.get("archived_sessions")==progress["archived_sessions"])
    official = admitted_rows(source,catalog)
    # Complete archive, not a claim of complete adjusted OHLCV. The scoring
    # replay already considered the saved price and official-close sources.
    # Keep a usable official part pending if the scorer did not admit it.
    def official_available(code, key):
        r = official.get(code,{})
        w = r.get("weekly") or {}
        if key=="daily_rsi": return r.get("daily_rsi") is not None
        if key=="ma20_ma60_cross_near_ma240": return r.get("daily_ma240_cross") is not None
        if key=="weekly_rsi": return w.get("rsi") is not None
        if key=="long_ma_position": return w.get("weekly_ma120_gap_pct") is not None
        return False
    members = {r["code"]:r for r in catalog["rows"]}
    reviews={}
    reasons=Counter()
    for code,row in report["rows"].items():
        for area,key in sorted(gaps[code]):
            label=":".join((code,area,key))
            if label in existing:
                continue
            reason=category(area,key,members[code].get("price_history_status"),False)
            if reason==SHORT_PRICE_HISTORY:
                continue
            why=None
            if reason=="chart_window_or_adjustment_review" and close_complete and official and not official_available(code,key):
                why="fixed official close archive complete; saved chart factor unavailable or invalid after scoring replay"
            elif area in ("financial","valuation") and not any(plans.values()):
                if key in ("revenue_growth","operating_growth_margin","sales_to_cap") and code not in pending_revenue:
                    why="fixed bulk and annual comparative plans exhausted; no eligible unreviewed original revenue receipt"
                elif key=="per_eps_quality_growth" and code not in pending_eps:
                    why="fixed EPS standard and exact-name plans exhausted; no eligible original EPS candidate"
                elif key=="balance_sheet_strength" or key=="pbr_equity_quality":
                    # Annual books must contain an actual attempted record.
                    book_row=next((r for r in book["records"] if r["code"]==code),None)
                    if book_row is not None:
                        why="fixed annual book attempted; bulk seed balance plan exhausted; valid remaining balance input unavailable"
                elif key in ("cashflow_fcf_persistence","fcf_yield") and code not in pending_fcf and read("v3-dart-cashflow-gap-summary.json")["source_eligible_unfetched_company_years"]==0:
                    why="fixed cashflow eligible collection exhausted; no remaining original-file candidate"
            if why:
                point=row["parts"][area].get(key)
                weight=round(R1D_PART_LIMITS[area][key]-(point[1] if point else 0),6)
                reviews[label]={"asof":day,"missing_weight":weight,"status":"source_missing_after_attempt","evidence":why}
                reasons[why]+=1
    summary={"fixed_source_plans":plans,"original_revenue_candidate_issuers":len(pending_revenue),
             "original_eps_candidate_issuers":len(pending_eps),"original_fcf_candidate_issuers":len(pending_fcf),
             "official_close_archive_complete":close_complete,"official_close_admitted_issuers":len(official),
             "terminal_review_counts":dict(reasons),"provider_calls":0}
    print(summary)
    return reviews
