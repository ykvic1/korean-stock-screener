"""Official OpenDART cash-flow taxonomy lookup for the missing-CAPEX audit.

Reads FSS's published XBRL standard-account list, NOT company statements.
A taxonomy ID with a plausible label is only an AUDIT CANDIDATE: it
cannot certify an issuer's sign, annual cash flow, or FCF score.
"""
from __future__ import annotations

import re

from v3_dart_cashflow_enrichment import INTANGIBLE_IDS, PPE_IDS

SECTIONS = ("CF1", "CF2", "CF3", "CF4")
MAX_TAXONOMY_CALLS = 4
ID = re.compile(r"(?:dart_|ifrs(?:-full)?_)[A-Za-z0-9]{2,120}\Z")
ID_PURCHASE = re.compile(r"Purchas|Acquir|Payment", re.I)
ID_EXCLUDE = re.compile(r"Proceeds|Disposal|Repayment|SaleOf|SalesOf", re.I)
NAME_PURCHASE = re.compile(r"취득|구입|매입|지급|purchase|acquisition|payment", re.I)
NAME_EXCLUDE = re.compile(r"처분|매각|매도|회수|상환|유입|sale|disposal|proceeds|repayment", re.I)
PPE = re.compile(r"유형\s*자산|건설중인자산|기계장치|property.*plant|equipment", re.I)
INTANGIBLE = re.compile(r"무형\s*자산|소프트웨어|산업재산권|개발비|intangible|software", re.I)


class OfficialTaxonomyError(ValueError):
    """Constant error codes only: never propagate auth keys or raw payloads."""


def candidate_kind(row, section):
    """Require official CF namespace, acquisition ID, matching acquisition label.

    Broad Korean labels or `dart_` prefix alone never authorize an ID.
    Ambiguous PPE/intangible or disposition-related rows are excluded.
    """
    if type(row) is not dict or row.get("sj_div") != section:
        return None
    code = row.get("account_id")
    if type(code) is not str or not ID.fullmatch(code):
        return None
    if not ID_PURCHASE.search(code) or ID_EXCLUDE.search(code):
        return None
    if row.get("data_tp") not in ("X", "(X)"):
        return None
    names = [row.get(field, "") for field in ("account_nm", "label_kor", "label_eng")]
    if any(type(n) is not str or len(n) > 160 for n in names):
        return None
    label = " ".join(names)
    if not NAME_PURCHASE.search(label) or NAME_EXCLUDE.search(label):
        return None
    ppe = bool(PPE.search(label))
    intangible = bool(INTANGIBLE.search(label))
    if ppe == intangible:
        return None
    return "ppe" if ppe else "intangible"


def official_candidates(key, session):
    """Exactly four authenticated GET calls; return public standard IDs only.

    Fails closed on malformed section data or ambiguity across the official
    taxonomies. No issuer IDs, source rows, API keys or URLs in exceptions.
    """
    if type(key) is not str or len(key) < 20:
        raise OfficialTaxonomyError("official_taxonomy_key_unavailable")
    observed = {"ppe": set(), "intangible": set()}
    for section in SECTIONS:
        try:
            response = session.get(
                "https://opendart.fss.or.kr/api/xbrlTaxonomy.json",
                params={"crtfc_key": key, "sj_div": section},
                timeout=(20, 45))
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise OfficialTaxonomyError("official_taxonomy_request_failed") from None
        if (type(payload) is not dict or payload.get("status") != "000" or
            type(payload.get("list")) is not list):
            raise OfficialTaxonomyError("official_taxonomy_invalid_response")
        rows = payload["list"]
        if len(rows) > 18000:
            raise OfficialTaxonomyError("official_taxonomy_excessive_rows")
        for row in rows:
            if type(row) is not dict:
                raise OfficialTaxonomyError("official_taxonomy_invalid_account")
            kind = candidate_kind(row, section)
            if kind is not None:
                observed[kind].add(row["account_id"])
    # A standardized ID that appears with two incompatible asset types is
    # not usable even as a single-kind audit hint.
    conflict = observed["ppe"] & observed["intangible"]
    for ids in observed.values():
        ids.difference_update(conflict)
    if any(len(ids) > 150 for ids in observed.values()):
        raise OfficialTaxonomyError("official_taxonomy_candidate_set_excessive")
    return observed


def novel_account_id(account_id, kind, candidate_ids):
    """Positive match is a lead for human review, NEVER a scorer allowlist."""
    existing = PPE_IDS if kind == "ppe" else INTANGIBLE_IDS
    return (type(account_id) is str and account_id not in existing and
            account_id in candidate_ids[kind])


# These account IDs identify disclosed components, not a complete CAPEX
# total. In particular, software can sit within intangible assets other
# than goodwill; adding the two may double-count the same outflow.
INTANGIBLE_POSSIBLE_PARENT = "dart_PurchaseOfIntangibleAssetsOtherThanGoodwill"
COMPONENT_GROUP_STATUSES = (
    "no_official_component",
    "invalid_or_missing_amount",
    "conflicting_duplicate_amounts",
    "negative_sign_unverified",
    "possible_parent_child_overlap",
    "multiple_components_completeness_unproven",
    "single_component_completeness_unproven",
)


def component_evidence_status(observations, kind):
    """Classify exact source-matched component *structure*, never sum it.

    `observations` holds ephemeral (standard_account_id, parsed_amount)
    pairs. Return only fixed anonymous labels. Multiple distinct components
    are NOT proof of a complete partition; one is NOT proof of a total.
    """
    if kind not in ("ppe", "intangible"):
        raise OfficialTaxonomyError("invalid_component_kind")
    if type(observations) is not list:
        raise OfficialTaxonomyError("invalid_component_observations")
    if not observations:
        return "no_official_component"
    amounts = {}
    for entry in observations:
        if (type(entry) not in (list, tuple) or len(entry) != 2 or
            type(entry[0]) is not str or not ID.fullmatch(entry[0])):
            return "invalid_or_missing_amount"
        ident, amount = entry
        if type(amount) is not int or abs(amount) > 10**18:
            return "invalid_or_missing_amount"
        amounts.setdefault(ident, set()).add(amount)
    if any(len(v) != 1 for v in amounts.values()):
        return "conflicting_duplicate_amounts"
    if any(next(iter(v)) < 0 for v in amounts.values()):
        return "negative_sign_unverified"
    if (kind == "intangible" and INTANGIBLE_POSSIBLE_PARENT in amounts
        and len(amounts) > 1):
        return "possible_parent_child_overlap"
    if len(amounts) > 1:
        return "multiple_components_completeness_unproven"
    return "single_component_completeness_unproven"
