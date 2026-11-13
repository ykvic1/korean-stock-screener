"""Fail-closed current-date reconciliation of official DART annual source changes.

Rechecks <=100 listed issuers per part and all four fiscal years via official
OpenDART. Only a strictly newer validated annual filing replaces its previous
derived record. Missing/unmapped/older/same-receipt-different-value evidence
is held for review, never treated as zero. In no mode is PIT certified.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import time

import v3_dart_bulk_history as bulk
import v3_dart_weekly_freshness as fresh

REPORT_SCHEMA = "v3-dart-official-current-annual-rebind-review-0.1"
MAX_REQUESTS = fresh.MAX_FISCAL_CALLS
FIELDS = frozenset(bulk.FIELDS)


class RebindError(ValueError):
    """A fixed message that cannot echo provider data or credentials."""


def _scope_valid(scopes, year, cutoff):
    if type(scopes) is not dict or not scopes or not set(scopes) <= {"CFS", "OFS"}:
        return False
    for key, item in scopes.items():
        if type(item) is not dict or not bulk._valid_receipt(
                item.get("receipt_id"), year, cutoff):
            return False
        values = item.get("values")
        ambiguous = item.get("ambiguous", [])
        if (type(values) is not dict or set(values) != FIELDS or
                type(ambiguous) is not list or any(x not in FIELDS for x in ambiguous)):
            return False
        if any(v is not None and
               (type(v) is not int or abs(v) > 10**18)
               for v in values.values()):
            return False
    return True


def _decision(before, after, year, cutoff):
    """Reason and adoption decision; no raw financial amounts leave caller."""
    if before == after:
        return "unchanged", False
    if not after:
        return "now_missing_keep_previous", False
    if not _scope_valid(after, year, cutoff):
        return "untrusted_refresh_keep_previous", False
    if not set(before) <= set(after):
        return "accounting_scope_lost_keep_previous", False
    for scope, old in before.items():
        prior = old.get("receipt_id")
        current = after[scope]["receipt_id"]
        if type(prior) is not str or not bulk._valid_receipt(prior, year, cutoff):
            return "untrusted_baseline_keep_previous", False
        if current == prior and after[scope] != old:
            return "same_receipt_data_changed_keep_previous", False
        if current < prior:
            return "receipt_regression_keep_previous", False
        old_values = old.get("values", {})
        new_values = after[scope]["values"]
        if any(type(old_values.get(field)) is int and
               new_values.get(field) is None for field in FIELDS):
            return "account_value_lost_keep_previous", False
    if before and all(after[scope]["receipt_id"] == before[scope]["receipt_id"]
                      for scope in before) and set(after) == set(before):
        # Other metadata changed under an identical receipt.
        return "same_receipt_metadata_changed_keep_previous", False
    return "newer_official_source_reconciled", True


def reconcile(market, catalog, book, archive, observed, *,
              when, part, selected=None, unmapped=(), verified_pairs=None):
    """Pure transform: never mutate original input nor call the provider."""
    state = fresh.prepare(market, catalog, archive, when, part)
    if not state["ready"]:
        raise RebindError("annual_baseline_first_pass_not_complete")
    if (type(book) is not dict or book.get("asof") != state["asof"] or
            book.get("market_listed_total") != len(state["members"])):
        raise RebindError("annual_book_asof_or_census_unaligned")
    if selected is not None and selected != state["selected"]:
        raise RebindError("source_recheck_selection_mismatch")
    if type(observed) is not dict or set(observed) != set(state["selected"]):
        raise RebindError("incomplete_verified_source_slice")
    unknown = set(unmapped)
    if not unknown <= set(state["selected"]):
        raise RebindError("unexpected_unmapped_source_issuer")
    expected_pairs={(code,str(year)) for code in state['selected']
                    if code not in unknown for year in state['fiscal']}
    if verified_pairs is None:
        verified_pairs=expected_pairs
    if type(verified_pairs) is not set or not verified_pairs<=expected_pairs:
        raise RebindError('unexpected_verified_source_pairs')
    cutoff = bulk._day(state["asof"])  # NEVER accept filings after market close.
    updated = deepcopy(archive)
    updated["asof"] = state["asof"]
    updated["fiscal_years"] = state["fiscal"]
    counts = {}
    flags = []
    edited = set()
    for code in state["selected"]:
        row = observed[code]
        if type(row) is not dict or set(row) != set(map(str, state["fiscal"])):
            raise RebindError("missing_verified_source_fiscal_year")
        for year in state["fiscal"]:
            if code in unknown:
                counts["unmapped_keep_previous"] = counts.get("unmapped_keep_previous", 0) + 1
                flags.append({"code": code, "fiscal_year": year,
                              "reason": "unmapped_keep_previous"})
                continue
            key = str(year)
            if (code,key) not in verified_pairs:
                counts['not_rechecked_keep_previous']=counts.get('not_rechecked_keep_previous',0)+1
                continue
            before = state["previous"][code]["years"][key]
            after = row[key]
            if type(before) is not dict or type(after) is not dict:
                raise RebindError("invalid_source_year_record")
            reason, replace = _decision(before, after, year, cutoff)
            counts[reason] = counts.get(reason, 0) + 1
            if reason != "unchanged":
                flags.append({"code": code, "fiscal_year": year, "reason": reason})
            if replace:
                updated["records"][code]["years"][key] = deepcopy(after)
                edited.add(code)
    fiscal = state["fiscal"]
    records = updated["records"]
    updated.update({
        "listed_total": len(state["members"]),
        "attempted_company_count": sum(
            all(str(y) in row["years"] for y in fiscal) for row in records.values()),
        "four_year_income_observed_count": sum(
            bulk._best_income_window(row, fiscal) is not None for row in records.values()),
        "completed_source_company_years": sum(
            len(row["years"]) for row in records.values()),
        "observed_nonempty_company_years": sum(
            sum(bool(x) for x in row["years"].values()) for row in records.values()),
        "unattempted_company_count": 0,
        "last_run_new_companies": 0,
        "last_run_reconciled_company_count": len(edited),
        "historical_PIT_source_certified": False,
        "four_year_complete_financial15_certified": False,
        "cfo_capex_and_net_debt_from_bulk_api_available": False,
        "raw_source_rows_published": False,
    })
    research = bulk.research_parts(updated, catalog, book)
    report = {
        "schema": REPORT_SCHEMA,
        "run_kst": when.isoformat(), "market_asof": state["asof"],
        "financial_baseline_asof": state["baseline_asof"],
        "cohort_index": state["slot"], "part_index": part,
        "selected_company_count": len(state["selected"]),
        "verified_company_year_count": len(verified_pairs),
        "not_rechecked_company_year_count": len(expected_pairs)-len(verified_pairs),
        "reconciled_company_count": len(edited),
        "reconciled_company_year_count": counts.get("newer_official_source_reconciled", 0),
        "reason_counts": dict(sorted(counts.items())),
        "review_flags": flags,
        "market_listed_total": len(state["members"]),
        "historical_PIT_certified": False,
        "full_V3_100point_certified": False,
        "buy_recommendations_authorized": False,
        "raw_official_rows_stored": False,
        "unmapped_source_years_promoted": False,
        "source_archive_changed": bool(edited),
    }
    return report, updated, research


def _source_wait_reason(error):
    """Only fixed transport/budget codes defer; schema/auth failures block."""
    reason=str(error)
    if reason in ('dart_bulk_http_timeout','dart_bulk_http_connection_failed'):
        return 'waiting_annual_source_transport'
    if reason in ('dart_bulk_safe_source_call_budget_reached',
                  'dart_bulk_safe_elapsed_time_budget_reached'):
        return 'waiting_annual_source_budget'
    if reason=='dart_bulk_http_status_failed':
        status=getattr(error,'http_status',None)
        if status==429:return 'waiting_annual_source_rate_limit'
        if type(status) is int and 500<=status<=599:return 'waiting_annual_source_service'
    if reason=='dart_bulk_provider_stop_or_schema':
        status=getattr(error,'provider_status',None)
        if status=='020':return 'waiting_annual_source_quota'
        if status in ('800','900'):return 'waiting_annual_source_service'
    return None


class _RequestMeter:
    def __init__(self,session):
        self.session=session
        self.calls=0

    def get(self,*args,**kwargs):
        self.calls+=1  # failed issued requests count too; never store URLs/keys
        return self.session.get(*args,**kwargs)


def execute(market, catalog, book, archive, *,
            when, part, mode, key=None, session=None, company_cache=None):
    if mode not in ("audit", "live", "publish"):
        raise RebindError("unsupported_rebind_mode")
    state = fresh.prepare(market, catalog, archive, when, part)
    if not state["ready"]:
        return {"schema": REPORT_SCHEMA, "status": "waiting_annual_backfill",
                "source_api_calls": 0, "source_archive_changed": False}, None, None
    if (type(book) is not dict or book.get("asof") != state["asof"] or
            book.get("market_listed_total") != len(state["members"])):
        return {"schema": REPORT_SCHEMA, "status": "waiting_aligned_annual_book",
                "source_api_calls": 0, "source_archive_changed": False}, None, None
    if mode == "audit":
        return {"schema": REPORT_SCHEMA, "status": "audit_plan_only",
                "source_api_calls": 0,
                "selected_company_count": len(state["selected"]),
                "source_archive_changed": False}, None, None
    if type(key) is not str or len(key) < 20 or session is None:
        raise RebindError("source_provider_missing")
    # Reuse the verified, exact-census, SAME-market-day company master from
    # the separately scheduled four-year collector. Never trust a missing,
    # stale or hash-mismatched cache as fresh official identity evidence.
    try:
        mapping = bulk.verified_company_mapping_cache(
            company_cache, state["asof"], state["members"])
    except bulk.BulkHistoryError:
        raise RebindError("untrusted_official_company_mapping_cache") from None
    cache_used = mapping is not None
    master_calls = 0
    if mapping is None:
        waiting = None
        for attempt in range(2):
            try:
                master_calls += 1
                resp = session.get(
                    "https://opendart.fss.or.kr/api/corpCode.xml",
                    params={"crtfc_key": key}, timeout=(20, 95))
                resp.raise_for_status()
                # Validate the full CURRENT market census, not only this
                # partition; mirror the existing source collector's mapping.
                mapping = bulk._mapping(resp.content, set(state["members"]))
                break
            except bulk.BulkHistoryError:
                # Malformed ZIP, duplicate identity or invalid identifiers
                # are SOURCE INTEGRITY failures, never temporary 'missing'.
                raise RebindError("untrusted_official_company_master") from None
            except Exception as error:
                # Retry only transport outages and HTTP 5xx once. Do not
                # retry 401/403 or provider schema errors as stale evidence.
                kind = type(error).__name__
                transient = kind in (
                    "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout")
                if kind == "HTTPError":
                    code = getattr(getattr(error, "response", None),
                                   "status_code", None)
                    if code == 429:
                        waiting = "waiting_official_company_master_rate_limit"
                        break
                    transient = type(code) is int and 500 <= code <= 599
                if transient and attempt == 0:
                    time.sleep(1)
                    continue
                if transient:
                    waiting = "waiting_official_company_master_transport"
                    break
                raise RebindError("official_company_master_auth_or_http_error") from None
        if waiting is not None:
            # A temporary provider outage must not clear the independently
            # saved per-issuer score or trigger an empty-score publication.
            return {
                "schema": REPORT_SCHEMA, "status": waiting,
                "market_asof": state["asof"],
                "run_kst": when.isoformat(), "part_index": part,
                "selected_company_count": len(state["selected"]),
                "source_api_calls": master_calls,
                "source_archive_changed": False,
                "review_flags": [{"reason": waiting}],
                "historical_PIT_certified": False,
                "full_V3_100point_certified": False,
                "buy_recommendations_authorized": False,
            }, None, None
    # Missing corporation mappings are quarantined individually; correctly
    # mapped issuers are still eligible for official validated refresh.
    unmapped = set(state["selected"]) - set(mapping)
    # Source callbacks arrive only AFTER a complete, normalized response.
    # Retain a separate pair mask so unrequested baseline rows can never be
    # mistaken for newly verified or newly missing observations.
    completed={}
    def checkpoint(records,year):
        if (year not in state['fiscal'] or type(records) is not dict
            or not set(records)<=set(state['selected'])-unmapped):
            raise RebindError('invalid_verified_source_checkpoint')
        for code,years in records.items():
            if type(years) is not dict or set(years)!={str(year)}:
                raise RebindError('invalid_verified_source_checkpoint')
            pair=(code,str(year))
            if pair in completed:raise RebindError('duplicate_verified_source_checkpoint')
            completed[pair]=deepcopy(years[str(year)])
    metered=_RequestMeter(session)
    waiting=None
    try:
        observed, requests_made = bulk.fetch_group(
            metered, key, state["asof"], state["fiscal"],
            state["selected"], mapping,
            prior_years={code: {} for code in state["selected"]},
            on_year=checkpoint,
            max_requests=MAX_REQUESTS,
            deadline=time.monotonic() + fresh.MAX_SOURCE_SECONDS)
    except bulk.BulkHistoryError as error:
        waiting=_source_wait_reason(error)
        if waiting is None:
            failure=RebindError("source_provider_quota_or_validation_stop")
            safe_reasons={'dart_bulk_provider_stop_or_schema','dart_bulk_http_status_failed',
                'dart_bulk_invalid_json','dart_bulk_unexpected_response',
                'dart_bulk_empty_success_response','dart_bulk_fatal_no_trustworthy_source_rows'}
            failure.report={'schema':REPORT_SCHEMA,'status':'blocked_annual_source_validation',
                'reason':str(error) if str(error) in safe_reasons else 'unclassified_source_validation',
                'source_api_calls':master_calls+metered.calls,'source_archive_changed':False,
                'validated_pairs_discarded':len(completed),'historical_PIT_certified':False,
                'full_V3_100point_certified':False,'buy_recommendations_authorized':False}
            raise failure from None
        requests_made=metered.calls
        if not completed:
            return {'schema':REPORT_SCHEMA,'status':waiting,'market_asof':state['asof'],
                'run_kst':when.isoformat(),'part_index':part,
                'source_api_calls':master_calls+requests_made,'source_archive_changed':False,
                'verified_company_year_count':0,'historical_PIT_certified':False,
                'full_V3_100point_certified':False,'buy_recommendations_authorized':False},None,None
        observed={code:{str(y):{} for y in state['fiscal']} for code in state['selected']}
        for (code,year),value in completed.items():observed[code][year]=value
    if not 0 <= requests_made <= MAX_REQUESTS:
        raise RebindError("source_budget_exceeded")
    report, updated, score = reconcile(
        market, catalog, book, archive, observed,
        when=when, part=part, selected=state["selected"], unmapped=unmapped,
        verified_pairs=set(completed) if waiting else None)
    report["source_api_calls"] = master_calls + requests_made
    report["company_master_cache_used"] = cache_used
    report["company_master_source_api_calls"] = master_calls
    report["unmapped_issuer_count"] = len(unmapped)
    report["status"] = ("verified_candidate_for_publication" if mode == "publish"
                        else "verified_live_candidate_not_published")
    if waiting:
        report['status']='partial_'+report['status']
        report['waiting_reason']=waiting
        report['source_slice_requests_completed']=False
    else:
        report['source_slice_requests_completed']=True
    return report, updated, score


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("audit", "live", "publish", "verify"), default="audit")
    p.add_argument("--run-day", required=True)
    p.add_argument("--part", required=True, type=int, choices=range(4))
    p.add_argument("--market", default="docs/data.json")
    p.add_argument("--catalog", default="docs/v22-market-catalog.json")
    p.add_argument("--book", default="docs/v3-dart-book-reference.json")
    p.add_argument("--history", default="docs/v3-dart-bulk-history.json")
    p.add_argument("--scores", default="docs/v3-dart-bulk-provisional.json")
    p.add_argument("--report", default="reports/v3-dart-weekly-rebind-review.json")
    p.add_argument("--company-cache", default=".cache/v3-dart-company-codes.json")
    args = p.parse_args(argv)
    when = bulk._day(args.run_day)
    data = [json.loads(Path(path).read_text(encoding="utf8")) for path in (
        args.market, args.catalog, args.book, args.history, args.scores)]
    market, catalog, book, archive, existing_score = data
    # A normal current-price/annual-book date rollover must defer, not
    # reinterpret an older research archive as current financial evidence.
    if (archive.get("asof") != catalog.get("date") or
            book.get("asof") != catalog.get("date")):
        report = {"schema": REPORT_SCHEMA, "status": "waiting_aligned_sources",
                  "source_api_calls": 0, "source_archive_changed": False}
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf8")
        print(json.dumps(report, sort_keys=True))
        return 0
    # Prove original sources and saved provisional points are coherent
    # before making any provider call or proposing a transaction.
    if bulk.research_parts(archive, catalog, book) != existing_score:
        # Annual book and the independently scheduled 4Y replayer publish at
        # different times. Defer transient source/score drift without DART
        # calls, writes or a false claim of verified rebind completion.
        # Post-rebase verification remains strict: never push mismatched data.
        if args.mode == "verify":
            raise RebindError("existing_annual_source_and_scores_disagree")
        report = {
            "schema": REPORT_SCHEMA,
            "status": "waiting_annual_score_replay",
            "source_api_calls": 0,
            "source_archive_changed": False,
            "market_asof": catalog["date"],
            "part_index": args.part,
            "review_flags": [{"reason": "saved_scores_disagree_with_current_annual_sources"}],
            "historical_PIT_certified": False,
            "full_V3_100point_certified": False,
            "buy_recommendations_authorized": False,
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf8")
        print(json.dumps({"status": report["status"], "source_api_calls": 0},
                         sort_keys=True))
        return 0
    if args.mode == "verify":
        print("source_rebind_post_rebase_verified")
        return 0
    company_cache = None
    if args.mode != "audit":
        import os
        import requests
        key = os.environ.get("DART_API_KEY")
        session = requests.Session()
        cache_path = Path(args.company_cache)
        if cache_path.is_file():
            try:
                company_cache = json.loads(cache_path.read_text(encoding="utf8"))
            except (OSError, ValueError, UnicodeError):
                raise RebindError("untrusted_official_company_mapping_cache") from None
    else:
        key = None
        session = None
    try:
        report, updated, score = execute(
            market, catalog, book, archive, when=when,
            part=args.part, mode=args.mode, key=key, session=session,
            company_cache=company_cache)
    except RebindError as error:
        if hasattr(error,'report'):
            report_path=Path(args.report)
            report_path.parent.mkdir(parents=True,exist_ok=True)
            report_path.write_text(json.dumps(error.report,sort_keys=True)+'\n',encoding='utf8')
        raise
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")) + "\n", encoding="utf8")
    if args.mode == "publish" and updated is not None and report["source_archive_changed"]:
        bulk.write_json(args.history, updated)
        bulk.write_json(args.scores, score)
    print(json.dumps({k: report.get(k) for k in (
        "status", "source_api_calls", "selected_company_count",
        "reconciled_company_count", "source_archive_changed",
    )}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
