"""Verify a structure-only replay of exactly the last observed money source."""
from copy import deepcopy
from v3_actual_money_structure import valid


def verify(previous, replayed):
    old,new=deepcopy(previous),deepcopy(replayed)
    recovered=0
    if set(old['records'])!=set(new['records']):
        raise ValueError('saved_money_replay_issuer_set_changed')
    for code,before in old['records'].items():
        after=new['records'][code]
        a=before.pop('r1c_structure',None)
        b=after.pop('r1c_structure',None)
        if not valid(a) or not valid(b):
            raise ValueError('saved_money_replay_structure_not_verified')
        av=a['components']['correction_contraction']
        bv=b['components']['correction_contraction']
        if av is not None and bv!=av:
            raise ValueError('saved_money_replay_existing_correction_changed')
        recovered+=av is None and bv is not None
        for value in (a,b):
            value.pop('correction_window_cohorts',None)
            value['components'].pop('correction_contraction')
        if a!=b or before!=after:
            raise ValueError('saved_money_replay_unrelated_points_or_dates_changed')
    # No new catalog/date/private revision, base participation points, source
    # checkpoints, coverage claims, or issuer identity can be smuggled in.
    if old!=new:
        raise ValueError('saved_money_replay_source_metadata_changed')
    return recovered
