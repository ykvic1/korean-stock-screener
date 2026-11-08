"""Admit only independently audited actual EPS to the research score layer."""
from collections import Counter
from copy import deepcopy
import v3_eps_basis_audit as audit
from v3_dart_bulk_history import _stable_digest


def admit(catalog,book,source,bulk,cash,research,evidence):
    accepted={};summary={'eligible':0,'audited':0,'accepted':0,'reasons':{},'provider_calls':0}
    if (not all(isinstance(s,dict) for s in (book,source,bulk,cash,research,evidence))
        or not isinstance(source.get('source_sha256'),dict)
        or source['source_sha256'].get('annual_book')!=_stable_digest(book)
        or evidence.get('schema')!=audit.SCHEMA or evidence.get('asof')!=catalog['date']
        or evidence.get('scores_unchanged') is not True or type(evidence.get('records')) is not dict):
        return accepted,summary
    try:contexts=audit.contexts(catalog,book,source,bulk,cash,research)
    except (ValueError,KeyError,TypeError):return accepted,summary
    summary['eligible']=len(contexts);reasons=Counter()
    for code,context in contexts.items():
        record=evidence['records'].get(code)
        if record is None:continue
        summary['audited']+=1
        try:
            valid=(record['asof']==catalog['date'] and audit.verify_for_context(record,context))
        except (ValueError,KeyError,TypeError):valid=False
        if not valid:
            reasons['not_qualified']+=1;continue
        # Retain original single-filing source untouched. Cross-filing values
        # live only in this explicit derived layer with the audit digest.
        entry=deepcopy(context['baseline'])
        entry['reported_basic_eps_2023_2025']=record['evidence']['eps'].copy()
        entry['metrics']=audit.eps.metrics(entry['reported_basic_eps_2023_2025'])
        entry['observed_on']=min(entry['observed_on'],record['asof'],context['research']['observed_on'])
        entry['comparative_basis']=record['contract']
        entry['basis_audit_digest']=record['digest']
        accepted[code]=entry;reasons[record['reason']]+=1
    summary.update(accepted=len(accepted),reasons=dict(reasons))
    return accepted,summary
