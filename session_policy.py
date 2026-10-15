"""KRX regular-session evidence contract. Missing evidence never earns a score."""
import hashlib,json
POLICY_VERSION='krx-regular-1'
REQUIRED_SESSION='KRX_REGULAR_0900_1530_ASIA_SEOUL'
FAQ='https://openapi.krx.co.kr/contents/OPP/COMM/faq/OPPCOMM004.cmd'

def assess(evidence):
    e=evidence or {}; reasons=[]
    for key in ('price_session','volume_session'):
        if e.get(key)!=REQUIRED_SESSION:reasons.append(key+'_unverified')
    if e.get('final') is not True:reasons.append('session_not_final')
    if e.get('official_close_status')!='matched':reasons.append('official_close_not_matched')
    if not e.get('official_source_sha256'):reasons.append('official_evidence_missing')
    if e.get('history_session_verified') is not True:reasons.append('indicator_history_session_unverified')
    if not e.get('asof') or not e.get('received_at'):reasons.append('provenance_incomplete')
    return {'policy_version':POLICY_VERSION,'required_session':REQUIRED_SESSION,
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
