"""Regular-close / era-specific volume evidence contract; no source inference."""
import hashlib,json
from datetime import date, datetime
from v3_aftermarket_cutoff import NXT_START, target_policy, AftermarketCutoffError
POLICY_VERSION='krx-close-era-volume-2'
REQUIRED_SESSION='KRX_REGULAR_0900_1530_ASIA_SEOUL'
FAQ='https://openapi.krx.co.kr/contents/OPP/COMM/faq/OPPCOMM004.cmd'

CONSOLIDATED_VOLUME='KRX_NXT_ALL_SESSION_VOLUME_TO_2000'


def required_volume_session(asof):
    """Pre-NXT regular-only; thereafter both venues including after-market.

    The date selects a requirement, never proves a provider satisfies it.
    Keep the old price-session identifier for callers that import it.
    """
    try:
        day=date.fromisoformat(asof)
    except (TypeError, ValueError):
        raise ValueError('invalid_session_market_date') from None
    if day.isoformat()!=asof:
        raise ValueError('invalid_session_market_date')
    return CONSOLIDATED_VOLUME if day>=NXT_START else REQUIRED_SESSION


def assess(evidence):
    e=evidence or {}; reasons=[]
    try:
        volume_session=required_volume_session(e.get('asof'))
    except ValueError:
        volume_session=None
        reasons.append('session_market_date_unverified')
    if e.get('price_session')!=REQUIRED_SESSION:reasons.append('price_session_unverified')
    if volume_session is None or e.get('volume_session')!=volume_session:
        reasons.append('volume_session_unverified')
    if e.get('final') is not True:reasons.append('session_not_final')
    try:
        received=datetime.fromisoformat(e.get('received_at'))
        if not target_policy(e.get('asof'),now=received)['complete_session_by_clock']:
            reasons.append('session_received_before_cutoff')
    except (TypeError, ValueError, AftermarketCutoffError):
        reasons.append('session_receipt_time_unverified')
    if e.get('official_close_status')!='matched':reasons.append('official_close_not_matched')
    if not e.get('official_source_sha256'):reasons.append('official_evidence_missing')
    if e.get('history_session_verified') is not True:reasons.append('indicator_history_session_unverified')
    if not e.get('asof') or not e.get('received_at'):reasons.append('provenance_incomplete')
    return {'policy_version':POLICY_VERSION,'required_session':REQUIRED_SESSION,
            'required_price_session':REQUIRED_SESSION,'required_volume_session':volume_session,
            'confirmed_scoring_allowed':not reasons,'reasons':reasons,
            'prediction_validation_required':False}

def input_fingerprint(rows,evidence):
    """Changing any price/volume/session evidence invalidates dependent calculations."""
    return hashlib.sha256(json.dumps({'rows':rows,'evidence':evidence,'policy':POLICY_VERSION},sort_keys=True,allow_nan=False).encode()).hexdigest()

def unverified(asof,source,received_at=None):
    e={'asof':asof,'source':source,'received_at':received_at,'price_session':'UNVERIFIED',
       'volume_session':'UNVERIFIED','official_close_status':'unverified','final':False,
       'history_session_verified':False,'official_source_sha256':None}
    return {**e,**assess(e)}
