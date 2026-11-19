"""Replay historical share-count changes on the pinned public market panel."""

import argparse
from collections import Counter
from fractions import Fraction
from hashlib import sha256
import json
import re
from pathlib import Path

from v3_forward_share_actions import (
    DART_TERMS, HOLDER_NEUTRAL_ACTIONS, KIND_LISTING_DETAIL, NOTICE, REGISTER,
    load_forward_actions, reviewed_actions, share_transition, valid_share_terms,
)
from v3_historical_market_coverage import END, ENTRY, HORIZONS, MARKETS, ROOT, SIGNAL, TAG
from v3_historical_market_coverage import _anniversary, day, ledger_rows, read_panels, require, traded
from v3_historical_performance_pilot_audit import successor_listing_counts
from v3_historical_pilot_daily_replay import CALENDAR_CODES, SOURCE_DIGESTS
from v3_krx_close_momentum_recovery import NOTICE_FILE, krx_tick, load_face_notices

CONTINUITY_REGISTER = Path("ops") / f"v3-performance-continuity-actions-{TAG}.json"


def official_actions(register, face_register, historical_register):
    """Reuse vetted listing and face-value notices with direct exchange links."""
    raw = register.read_bytes()
    actions = reviewed_actions(raw)
    historical_raw = historical_register.read_bytes()
    historical = reviewed_actions(historical_raw)
    require(not actions.keys() & historical.keys(), "duplicate_historical_share_action")
    actions.update(historical)
    faces, face_digest = load_face_notices(face_register)
    for code, notices in faces.items():
        for session, item in notices.items():
            urls = (item.get("listing_notice_url"), item.get("face_evidence_url"), item.get("kind_url"))
            url = next((url for url in urls if url and NOTICE.fullmatch(url)
                        and url.rsplit("/", 1)[-1] in {"68155.htm", "70764.htm", "70795.htm"}), None)
            if not url or (session, code) in actions:
                continue
            ratio = Fraction(item["old_face_krw"], item["new_face_krw"])
            actions[session, code] = {
                "market": item.get("market", "KOSDAQ"), "notice_url": url,
                "action": "split" if ratio > 1 else "reverse_split",
                "new_units": ratio.numerator, "old_units": ratio.denominator,
                "shares_before": item.get("listed_shares_before"),
                "shares_after": item.get("listed_shares_after"),
            }
    return actions, {"listing_register": sha256(raw).hexdigest(), "face_register": face_digest,
                     "historical_register": sha256(historical_raw).hexdigest()}


COMBINED_CANCELLATION = "treasury_cancellation_then_reverse_split"
SPIN_OFF = "spin_off"
TWO_SOURCE_REDUCTIONS = {"reverse_split", "capital_reduction"}
SUCCESSOR_ISSUANCE = "successor_share_issuance"
PAID_RIGHTS_LISTING = "paid_rights_listing_without_entitlement"
PAID_RIGHTS_UNPRICED = "paid_rights_entitlement_unpriced"
SHARE_COUNT_INCREASES = {"third_party_placement", "bond_conversion", "option_exercise",
                         "preferred_to_common", "public_offering", "bond_warrant_exercise", SUCCESSOR_ISSUANCE}
CONTINUITY_KINDS = SHARE_COUNT_INCREASES | {"treasury_cancellation", COMBINED_CANCELLATION,
                                          SPIN_OFF, PAID_RIGHTS_LISTING, "rename"}
SPIN_OFF_LISTING = re.compile(
    r"https://kind\.krx\.co\.kr/corpgeneral/stockissuelist\.do\?bzProcsNo=(\d{14})"
    r"&contnId=\1&forward=searchStockIssueDetail&method=searchStockIssueDetail\Z"
)
TREASURY_LISTING_COPY = re.compile(
    r"https://(?:spn\.stockplus\.com/news/api/v1/disclosure_views/koscom/[0-9]+"
    r"|stock\.mk\.co\.kr/news/disclosure/template/[0-9]+"
    r"|invest\.kiwoom\.com/inv/disclosure_detail\?[^\s#]+)\Z"
)
TREASURY_TERMS_COPY = re.compile(
    r"https://(?:spn\.stockplus\.com/news/api/v1/disclosure_views/koscom/[0-9]+"
    r"|stock\.mk\.co\.kr/news/disclosure/template/[0-9]+"
    r"|samara\.kr/disclosure/[0-9]{14}"
    r"|cdn\.financialreports\.eu/(?:financialreports/media/filings|processed/html)/[A-Za-z0-9/_-]+\.html"
    r"|www\.awakeplus\.co\.kr/data/view/[0-9]{14}"
    r"|etp\.nhqv\.com/publicannounce/[0-9]+"
    r"|www\.samsung\.com/sec/ir/stock-information/listing-info/)\Z"
)
SUSPENDED_TREASURY_VALUES = ("resumed_on", "split_shares", "reduction_shares")
SUSPENDED_TREASURY_SOURCES = ("split_url", "reduction_url", "resume_url")
SUSPENDED_TREASURY_FIELDS = SUSPENDED_TREASURY_VALUES + SUSPENDED_TREASURY_SOURCES
COMBINED_CANCELLATION_FIELDS = ("cancelled_shares", "reverse_split_url", "halt_from", "halt_url", "resume_url")
SPIN_OFF_FIELDS = ("halt_from", "halt_url", "child_listing_url", "child_code", "child_name",
                   "child_shares", "child_new_units", "child_old_units")


def validate_spin_off(notice):
    before, after = notice["shares_before"], notice["shares_after"]
    parent_new, parent_old = notice["new_units"], notice["old_units"]
    sources = [notice[field] for field in ("terms_url", "halt_url", "child_listing_url")]
    positive = (parent_new, parent_old, notice["child_new_units"], notice["child_old_units"],
                notice["child_shares"])
    require(all(type(n) is int and n > 0 for n in positive)
            and 0 < after < before and parent_new < parent_old
            and before * parent_new // parent_old == after
            and re.fullmatch(r"[0-9A-Z]{6}", notice["child_code"])
            and notice["child_code"] != notice["code"]
            and isinstance(notice["child_name"], str) and notice["child_name"].strip()
            and day(notice["halt_from"]) < day(notice["listed_on"])
            and len(set(sources + [notice["notice_url"]])) == 4
            and all(NOTICE.fullmatch(url) for url in sources), "invalid_spin_off_terms")


def continuity_actions(raw):
    """Read reviewed terms. Adjacent source rows must also match these terms."""
    actions = {}
    for notice in json.loads(raw):
        action = notice["action"]
        code = notice["code"]
        before, after = notice["shares_before"], notice["shares_after"]
        listing_url = notice["notice_url"]
        treasury = action == "treasury_cancellation"
        listing_source = (NOTICE.fullmatch(listing_url)
                          or treasury and TREASURY_LISTING_COPY.fullmatch(listing_url)
                          or action == SPIN_OFF and SPIN_OFF_LISTING.fullmatch(listing_url)
                          or action in TWO_SOURCE_REDUCTIONS and KIND_LISTING_DETAIL.fullmatch(listing_url))
        require(re.fullmatch(r"[0-9A-Z]{6}", code) and notice["market"] in MARKETS.values()
                and all(type(n) is int and n > 0 for n in (before, after))
                and all(isinstance(notice[k], str) and notice[k].strip()
                        for k in ("name_before", "name_after"))
                and listing_source, "invalid_continuity_notice")
        require((notice["name_before"] != notice["name_after"]) == (action == "rename"),
                "invalid_continuity_notice_names")
        listed_on = day(notice["listed_on"]).isoformat()
        key = listed_on, code
        require(key not in actions, "duplicate_continuity_listing_notice")
        if action == "bonus_issue":
            rights_on = day(notice["ex_on"]).isoformat()
            ratio = Fraction(notice["new_units"], notice["old_units"])
            eligible = notice["eligible_shares"]
            require((rights_on, code) not in actions and rights_on < listed_on
                    and ratio > 1 and type(eligible) is int and 0 < eligible <= before
                    and after - before == int(eligible * (ratio - 1))
                    and type(notice["ex_reference_krw"]) is int and notice["ex_reference_krw"] > 0
                    and NOTICE.fullmatch(notice["rights_url"]) and NOTICE.fullmatch(notice["terms_url"]),
                    "invalid_bonus_rights_notice")
            actions[rights_on, code] = {
                **notice, "action": "bonus_ex_rights", "notice_url": notice["rights_url"],
            }
            actions[key] = {**notice, "action": "bonus_listing", "new_units": 1, "old_units": 1}
        elif action == "paid_rights_issue":
            ex_on = day(notice["ex_on"])
            rights_url, terms_url = notice["rights_url"], notice["terms_url"]
            require(ex_on < day(listed_on) and 0 < before < after
                    and len({listing_url, rights_url, terms_url}) == 3
                    and NOTICE.fullmatch(rights_url) and NOTICE.fullmatch(terms_url),
                    "invalid_paid_rights_terms")
            if ex_on > ENTRY:
                ex_key = ex_on.isoformat(), code
                require(ex_key not in actions, "duplicate_paid_rights_session")
                actions[ex_key] = {**notice, "action": PAID_RIGHTS_UNPRICED}
            else:
                actions[key] = {**notice, "action": PAID_RIGHTS_LISTING, "new_units": 1, "old_units": 1}
        elif action == SPIN_OFF:
            validate_spin_off(notice)
            actions[key] = notice
        elif action in TWO_SOURCE_REDUCTIONS:
            new, old = notice.get("new_units"), notice.get("old_units")
            terms_url = notice.get("terms_url", "")
            require(KIND_LISTING_DETAIL.fullmatch(listing_url) and DART_TERMS.fullmatch(terms_url)
                    and all(type(n) is int for n in (new, old))
                    and valid_share_terms(action, before, after, new, old),
                    "invalid_two_source_share_reduction")
            actions[key] = {**notice, "name": notice["name_after"]}
        elif action == COMBINED_CANCELLATION:
            cancelled = notice.get("cancelled_shares")
            old_units = notice.get("old_units")
            terms_url = notice.get("terms_url", "")
            sources = {notice.get(field, "") for field in ("reverse_split_url", "halt_url", "resume_url")}
            require(type(cancelled) is int and 0 < cancelled < before
                    and type(old_units) is int and old_units > 1
                    and before - cancelled == after * old_units
                    and day(notice["halt_from"]).isoformat() < listed_on
                    and len(sources | {listing_url, terms_url}) == 5
                    and all(NOTICE.fullmatch(url) for url in sources)
                    and (NOTICE.fullmatch(terms_url) or TREASURY_TERMS_COPY.fullmatch(terms_url)),
                    "invalid_combined_cancellation_chain")
            actions[key] = {**notice, "new_units": 1}
        else:
            direction = (after > before) - (after < before)
            require((action in SHARE_COUNT_INCREASES and direction == 1)
                    or (action, direction) in {("rename", 0), ("treasury_cancellation", -1)},
                    "invalid_continuity_notice_kind")
            if treasury:
                terms_url = notice.get("terms_url", "")
                require(terms_url != listing_url and (NOTICE.fullmatch(terms_url)
                        or TREASURY_TERMS_COPY.fullmatch(terms_url)), "missing_treasury_cancellation_terms")
            if action == "public_offering":
                terms_url = notice.get("terms_url", "")
                require(terms_url != listing_url and NOTICE.fullmatch(terms_url)
                        and notice.get("existing_holder_rights") is False, "unreviewed_public_offering_terms")
            if action == SUCCESSOR_ISSUANCE:
                terms_url = notice.get("terms_url", "")
                exchanged = notice.get("exchanged_code", "")
                require(terms_url != listing_url and NOTICE.fullmatch(terms_url)
                        and re.fullmatch(r"[0-9A-Z]{6}", exchanged) and exchanged != code
                        and notice.get("existing_holder_rights") is False, "unreviewed_successor_issuance")
            if any(field in notice for field in SUSPENDED_TREASURY_FIELDS):
                require(treasury and all(field in notice for field in SUSPENDED_TREASURY_FIELDS),
                        "invalid_suspended_treasury_chain")
                resume = notice["resumed_on"]
                split_shares = notice.get("split_shares")
                reduction = notice.get("reduction_shares")
                chain_urls = {notice.get(field, "") for field in SUSPENDED_TREASURY_SOURCES}
                require(day(resume).isoformat() > listed_on
                        and type(split_shares) is int and split_shares > after
                        and split_shares % after == 0
                        and type(reduction) is int and split_shares - reduction == after
                        and len(chain_urls) == len(SUSPENDED_TREASURY_SOURCES)
                        and all(NOTICE.fullmatch(url) for url in chain_urls)
                        and not chain_urls & {listing_url, terms_url},
                        "invalid_suspended_treasury_chain")
            actions[key] = {**notice, "new_units": 1, "old_units": 1}
    for (session, code), notice in actions.items():
        if notice["action"] != "bonus_ex_rights" or "paid_listing_on" not in notice:
            continue
        paid_on = day(notice["paid_listing_on"]).isoformat()
        paid = actions.get((paid_on, code))
        require(session < paid_on < notice["listed_on"] and paid is not None
                and paid["action"] == PAID_RIGHTS_LISTING and paid["shares_after"] == notice["shares_before"]
                and all(paid[k] == notice[k] for k in ("market", "name_before", "name_after")),
                "invalid_bonus_intervening_paid_listing")
        notice["ex_shares"] = paid["shares_before"]
    return actions


def suspension_matches(panels, sessions, index, code, notice, expected_before):
    halt = day(notice["halt_from"])
    if halt not in sessions[1:index]:
        return False
    halt_index = sessions.index(halt)
    return (traded(panels[sessions[halt_index - 1]].get(code))
            and all((quote := panels[session].get(code, ()))[:3] == expected_before and quote[-1] == 0
                    for session in sessions[halt_index:index]))


def match_held_units(panels, sessions, index, code, notice):
    session = sessions[index]
    previous, current = (panels[s].get(code) for s in sessions[index - 1:index + 1])
    kind = notice["action"]
    if kind in HOLDER_NEUTRAL_ACTIONS and "name" in notice:
        if traded(current) and share_transition(previous and previous[:3], current[:3], notice) == "notice_matched":
            return Fraction(1)
        return None
    if kind not in CONTINUITY_KINDS | {"bonus_ex_rights", "bonus_listing"}:
        return matched_ratio(previous, current, notice)
    before, after = notice["shares_before"], notice["shares_after"]
    market = notice["market"]
    if kind == "bonus_ex_rights":
        before = notice.get("ex_shares", before)
    expected_before = market, notice["name_before"], before
    expected_after = market, notice["name_after"], after if kind != "bonus_ex_rights" else before
    if (not previous or not current or previous[:3] != expected_before or current[:3] != expected_after):
        return None
    if "resumed_on" in notice:
        resumed = day(notice["resumed_on"])
        if previous[-1] != 0:
            return None
        if resumed not in sessions[index + 1:]:
            return None
        for pending_session in sessions[index:]:
            quote = panels[pending_session].get(code)
            if not quote or quote[:3] != expected_after:
                return None
            if pending_session == resumed:
                return Fraction(1) if traded(quote) else None
            # A suspended quote can retain a stale close. Require explicit zero volume.
            if quote[-1] != 0:
                return None
    if not traded(current):
        return None
    if kind in {COMBINED_CANCELLATION, SPIN_OFF}:
        if not suspension_matches(panels, sessions, index, code, notice, expected_before):
            return None
    if kind == SPIN_OFF:
        child_code = notice["child_code"]
        child = panels[session].get(child_code)
        if (not traded(child) or child[:3] != (market, notice["child_name"], notice["child_shares"])
                or any(child_code in panels[s] for s in sessions[:index])):
            return None
    if kind == "bonus_ex_rights":
        # Require the later listing and stable trade identity throughout the entitlement window.
        listed_on = day(notice["listed_on"])
        if listed_on not in sessions[index + 1:] or not traded(previous):
            return None
        listed = panels[listed_on].get(code)
        paid_on = day(notice["paid_listing_on"]) if "paid_listing_on" in notice else listed_on
        if (not traded(listed) or listed[:3] != (market, notice["name_after"], after)
                or paid_on not in sessions or not traded(panels[paid_on].get(code))):
            return None
        for s in sessions[index:]:
            if s >= listed_on:
                break
            shares = before if s < paid_on else notice["shares_before"]
            if panels[s].get(code, ())[:3] != (market, notice["name_before"], shares):
                return None
        # Compare the issued shares used in the bonus terms, including any
        # previously issued shares that are still awaiting their listing.
        expected_reference = Fraction(int(previous[6]) * notice["shares_before"], after)
        reference = notice["ex_reference_krw"]
        if reference >= previous[6] or abs(reference - expected_reference) >= krx_tick(reference):
            return None
    return Fraction(notice["new_units"], notice["old_units"])


def matched_ratio(previous, current, notice):
    if (notice is None or previous is None or not traded(current)
            or previous[:2] != current[:2] or current[0] != notice["market"]):
        return None
    before, after = previous[2], current[2]
    if (before is None or after is None or before == after
            or not (0 < before == int(before) and 0 < after == int(after))):
        return None
    if "name" in notice:
        if share_transition(previous[:3], current[:3], notice) != "notice_matched":
            return None
    elif (notice["shares_before"] is not None and notice["shares_before"] != before
          or notice["shares_after"] is not None and notice["shares_after"] != after):
        return None
    ratio = Fraction(notice["new_units"], notice["old_units"])
    return ratio if int(int(before) * ratio) == after else None


def replay(panels, delisted, notices):
    sessions = sorted(session for session in panels if SIGNAL <= session <= END)
    require(sessions and sessions[0] == SIGNAL and sessions[1:2] == [ENTRY]
            and sessions[-1] == END, "historical_share_action_session_range")
    require(all(traded(panels[session].get(code)) for session in sessions for code in CALENDAR_CODES),
            "share_action_calendar_reference_without_trade")
    valuations = {next(s for s in sessions if s >= _anniversary(SIGNAL, h)): h for h in HORIZONS}
    require(len(valuations) == len(HORIZONS), "overlapping_share_action_horizons")

    by_session = {}
    rights_cutoffs = {}
    for (session, code), notice in notices.items():
        session = day(session)
        by_session.setdefault(session, {})[code] = notice
        if notice["action"] == PAID_RIGHTS_UNPRICED and code not in delisted and traded(panels[ENTRY].get(code)):
            require(session in sessions, "paid_rights_ex_date_outside_session_range")
            rights_cutoffs[code] = min(session, rights_cutoffs.get(code, session))
    first_changes = Counter()
    horizons = {str(h): [] for h in HORIZONS}
    anchor = panels[SIGNAL]
    observed = {}
    for index, session in enumerate(sessions[1:], 1):
        for code, notice in by_session.get(session, {}).items():
            if (notice["action"] == PAID_RIGHTS_UNPRICED or code in delisted
                    or code not in anchor or not traded(panels[ENTRY].get(code))):
                continue
            last, row = panels[sessions[index - 1]].get(code), panels[session].get(code)
            ratio = match_held_units(panels, sessions, index, code, notice)
            if ratio is not None:
                observed[session, code] = {
                    "code": code, "market": row[0], "name": row[1], "session": session.isoformat(),
                    "before": int(last[2]), "after": int(row[2]),
                    "new_units": ratio.numerator, "old_units": ratio.denominator,
                    "action": notice["action"], "notice_url": notice["notice_url"],
                    **{k: notice[k] for k in ("terms_url", "ex_on", "listed_on", "rights_url",
                                             "ex_reference_krw", "eligible_shares",
                                             *SUSPENDED_TREASURY_FIELDS,
                                             *COMBINED_CANCELLATION_FIELDS, *SPIN_OFF_FIELDS,
                                             "existing_holder_rights", "paid_listing_on", "ex_shares",
                                             "exchanged_code", "listing_notices") if k in notice},
                }
    incorporated = set()
    for code in sorted(anchor.keys() - delisted):
        entry = panels[ENTRY].get(code)
        if not traded(entry):
            continue
        units = Fraction(1)
        last = anchor[code]
        matched = False
        low = Fraction(int(entry[3]))
        path_gaps = 0
        pending_units = Fraction(0)
        buyer_without_entitlement = False
        entitlement_sessions = 0
        seen_share_change = False
        child_event = None
        child_units = Fraction(0)
        for session in sessions[1:]:
            if rights_cutoffs.get(code) == session:
                break
            row = panels[session].get(code)
            if row is None or row[0] != last[0]:
                break
            key = session, code
            event = observed.get(key)
            listed_share_change = not (last[2] and row[2]) or row[2] != last[2]
            if listed_share_change and not seen_share_change and row[1] == last[1]:
                first_changes["missing" if not (last[2] and row[2]) else
                              "increase" if row[2] > last[2] else "decrease"] += 1
                seen_share_change = True
            notice = by_session.get(session, {}).get(code)
            transition_required = listed_share_change or row[1] != last[1] or (
                notice is not None and notice["action"] == "bonus_ex_rights")
            if transition_required and event is None:
                break
            if event is not None:
                kind = event["action"]
                if kind == "bonus_listing" and not (pending_units or buyer_without_entitlement):
                    break
                if kind == SPIN_OFF:
                    if child_event or pending_units:
                        break
                    child_event = event
                    child_units = units * Fraction(event["child_new_units"], event["child_old_units"])
                incorporated.add(key)
                matched = True
                if kind == "bonus_ex_rights":
                    buyer_without_entitlement = session == ENTRY
                    if not buyer_without_entitlement:
                        pending_units = units * (Fraction(event["new_units"], event["old_units"]) - 1)
                elif kind == "bonus_listing":
                    pending_units = Fraction(0)
                    buyer_without_entitlement = False
                # Entry at the changed price has no earlier holdings or rights.
                if session != ENTRY:
                    units *= Fraction(event["new_units"], event["old_units"])
            if pending_units:
                entitlement_sessions += 1
            child = None
            if child_event:
                child = panels[session].get(child_event["child_code"])
                identity = child_event["market"], child_event["child_name"], child_event["child_shares"]
                if not child or child[:3] != identity:
                    break
            if traded(row) and (child_event is None or traded(child)):
                if child_event is None:
                    low = min(low, Fraction(int(row[5])) * units)
                if session in valuations and matched:
                    close = Fraction(int(row[6])) * units
                    extra = {}
                    adverse = round(float((1 - low / int(entry[3])) * 100), 4)
                    if child_event:
                        close += int(child[6]) * child_units
                        # The two intraday lows need not occur at the same time.
                        adverse = None
                        extra = {"child_code": child_event["child_code"],
                                 "child_units_per_entry_share": str(child_units),
                                 "valuation_basis": "proportional_spin_off_close"}
                    horizons[str(valuations[session])].append({
                        "code": code, "valuation_session": session.isoformat(),
                        "price_change_pct": round(float((close / int(entry[3]) - 1) * 100), 4),
                        "observed_adverse_excursion_pct": adverse,
                        "unpriced_sessions": path_gaps,
                        "unlisted_entitlement_sessions": entitlement_sessions,
                        "valuation_includes_unlisted_entitlement": bool(pending_units),
                        "tradable_units_per_entry_share": str(units - pending_units),
                        **extra,
                    })
            else:
                path_gaps += 1
            last = row
    return {
        "source_sha256": dict(zip(("2025_parquet", "2026_parquet", "2026-11-13_csv"), SOURCE_DIGESTS, strict=True)),
        "signal_session": SIGNAL.isoformat(), "entry_session": ENTRY.isoformat(),
        "first_share_count_changes": {kind: first_changes[kind] for kind in ("increase", "decrease", "missing")},
        "matched_events": [{**event, "incorporated_into_path": key in incorporated}
                           for key, event in sorted(observed.items())],
        "horizons": {key: {"count": len(rows), "rows": rows} for key, rows in horizons.items()},
        "unpriced_paid_rights_codes_by_horizon": {
            str(h): sorted(code for code, ex_on in rights_cutoffs.items() if ex_on <= session)
            for session, h in valuations.items()
        },
        "fractional_share_model": True, "cash_distributions_included": False,
        "unlisted_entitlement_valuation": "same_session_common_share_quote",
        "official_adjusted_history_verified": False, "performance_validated": False,
    }


def successor_issuance_actions(panels, listings, settlements):
    """Apply acquired shareholders’ new-share delivery to the existing issuer's share count."""
    terms = {row["code"]: row for row in settlements if row["treatment"] == "stock_exchange"}
    rows = [row for row in listings if row["treatment"] == "new_shares"]
    by_acquired = {row["code"]: row for row in rows}
    require(len(by_acquired) == len(rows) and by_acquired.keys() <= terms.keys(),
            "unmatched_successor_issuance_terms")
    successor_listing_counts(by_acquired, by_acquired.keys(), terms, {})
    sessions = sorted(panels)
    adjacent = dict(zip(sessions[1:], sessions[:-1], strict=True))
    actions = {}
    for row in rows:
        acquired = row["code"]
        code = row["successor_code"]
        terms_url = terms[acquired]["term_source_url"]
        require(re.fullmatch(r"[0-9A-Z]{6}", code) and code != acquired
                and terms_url.startswith("https://"), "invalid_successor_issuance_terms")
        listed = day(row["listing_on"])
        prior = day(row["prior_trade_on"])
        if adjacent.get(listed) != prior:
            continue
        previous = panels[prior].get(code)
        current = panels[listed].get(code)
        if not traded(previous) or not traded(current) or previous[:2] != current[:2]:
            continue
        market, name = current[:2]
        key = row["listing_on"], code
        require(key not in actions, "duplicate_successor_issuance")
        actions[key] = {
            "action": SUCCESSOR_ISSUANCE, "market": market, "name_before": name,
            "name_after": name, "shares_before": int(row["shares_before"]),
            "shares_after": int(row["shares_after"]), "new_units": 1, "old_units": 1,
            "notice_url": row["source_url"], "terms_url": terms_url, "exchanged_code": acquired,
        }
    return actions


def load_replay(panels):
    delisted = {r["code"] for r in ledger_rows(ROOT, "delistings") if r["anchor_membership"] == "present"}
    historical = ROOT / "ops" / f"v3-performance-reviewed-share-actions-{TAG}.csv"
    notices, digests = official_actions(ROOT / REGISTER, NOTICE_FILE, historical)
    raw = (ROOT / CONTINUITY_REGISTER).read_bytes()
    continuity = continuity_actions(raw)
    require(not notices.keys() & continuity.keys(), "duplicate_market_transition")
    digests["continuity_register"] = sha256(raw).hexdigest()
    ledgers = ("successor-listings", "delisting-settlement")
    issuance = successor_issuance_actions(panels, *(ledger_rows(ROOT, name) for name in ledgers))
    require(not (notices.keys() | continuity.keys()) & issuance.keys(), "duplicate_successor_market_transition")
    for name in ledgers:
        path = ROOT / "ops" / f"v3-performance-{name}-{TAG}.csv"
        digests[name.replace("-", "_") + "_register"] = sha256(path.read_bytes()).hexdigest()
    archived = {**notices, **continuity, **issuance}
    shared = {
        key: notice for key, notice in load_forward_actions(ROOT).items()
        if SIGNAL.isoformat() < key[0] <= END.isoformat() and key not in archived
        and notice["action"] in HOLDER_NEUTRAL_ACTIONS
    }
    digests["shared_forward_register"] = sha256(json.dumps(
        sorted(shared.items()), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    report = replay(panels, delisted, {**archived, **shared})
    report["register_sha256"] = digests
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs=3, type=Path, metavar="SOURCE")
    parser.add_argument("--verify", type=Path, help="Compare the recomputed report with a saved JSON file.")
    args = parser.parse_args()
    report = load_replay(read_panels(args.sources))
    if args.verify is not None:
        require(report == json.loads(args.verify.read_text(encoding="utf-8")), "share_action_report_mismatch")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
