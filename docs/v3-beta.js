/* v3 사용자 모의검증 베타. 기존 공개 v2.2 관측값을 활용하는 연구용 화면.
 * NO V3.0b five-area official investment score, NO observed future performance claim.
 * User's paper records reside in own browser localStorage only.
 */
(function(root) {
  'use strict';
  const STORAGE_KEY='kr-stock-v3-paper-beta-1';
  const ID=/^[0-9A-Z]{6}$/;
  const KIND_VALUES=new Set(['all','early','weekly','pattern','volume','obv','partial']);
  const RISK_VALUES=new Set(['all','controlled','unverified']);
  const STATUS_VALUES=new Set(['all','priced','missing']);
  const MARKET_VALUES=new Set(['all','KOSPI','KOSDAQ']);
  const clean=(x)=>String(x==null?'':x);
  const escapeHtml=(x)=>clean(x).replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
  const fin=(x)=>typeof x==='number'&&Number.isFinite(x);
  const n=(x)=>fin(x)?x:null;
  const money=(x)=>fin(x)?x.toLocaleString('ko-KR',{maximumFractionDigits:2})+'원':'자료 없음';
  const pct=(x)=>fin(x)?(x>0?'+':'')+x.toFixed(1)+'%':'미산출';
  const validDay=(x)=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(x)&&
    !Number.isNaN(Date.parse(x))&&new Date(x+'T00:00:00Z').toISOString().slice(0,10)===x;
  function parseSnapshot(x) {
    if (!x||x.version!=='2.0'||x.status!=='success'||!validDay(x.date)||
        !Array.isArray(x.stocks)||!x.stocks.length||x.stocks.length>6000) {
      throw Error('공개 가격 자료의 형식 또는 기준일 확인 실패');
    }
    let seen=new Set();
    const rows=[];
    for (const r of x.stocks) {
      if(!r||typeof r!=='object'||!ID.test(clean(r.code))||seen.has(r.code)||
          typeof r.name!=='string'||!r.name.trim()||!fin(r.price)||r.price<=0) continue;
      seen.add(r.code);
      rows.push(r);
    }
    if (!rows.length) throw Error('유효한 종목 가격이 없습니다');
    return {date:x.date,stocks:rows,total:x.stocks.length,flowConfirmedCount:x.flow_confirmed_count};
  }
  function mergeCatalog(view,catalog){
    if(!catalog||catalog.schema!=='v22-current-listed-catalog-0.1'||
       catalog.date!==view.date||!Array.isArray(catalog.rows)||
       catalog.rows.length!==catalog.listed_total||catalog.rows.length>6000||
       catalog.priced_count!==view.stocks.length) return null;
    const validStatuses=new Set(['priced','price_source_or_validation_error',
                                 'insufficient_history','unattempted_batch']);
    const existing=new Map(view.stocks.map(r=>[r.code,r]));
    const seen=new Set(),rows=[],counts={priced:0,price_source_or_validation_error:0,
      insufficient_history:0,unattempted_batch:0};
    for(const x of catalog.rows){
      if(!x||!ID.test(clean(x.code))||seen.has(x.code)||!validStatuses.has(x.price_history_status)||
          typeof x.name!=='string'||!x.name.trim()||!['KOSPI','KOSDAQ'].includes(x.market))return null;
      if(x.listing_zero_volume_reference!==undefined&&
         x.listing_zero_volume_reference!==null&&
         typeof x.listing_zero_volume_reference!=='boolean')return null;
      seen.add(x.code);counts[x.price_history_status]++;
      const zeroVolumeReference=x.listing_zero_volume_reference===undefined?
        null:x.listing_zero_volume_reference;
      const raw=existing.get(x.code);
      if(x.price_history_status==='priced'){
        if(!raw||raw.market!==x.market||raw.name!==x.name||raw.price!==x.published_price)return null;
        rows.push({...raw,price_history_status:'priced',
          reference_market_cap_eok:fin(x.reference_market_cap_eok)?x.reference_market_cap_eok:null,
          reference_trading_value_eok:fin(x.reference_trading_value_eok)?x.reference_trading_value_eok:null,
          listing_zero_volume_reference:zeroVolumeReference,
          pbr_observed:fin(x.pbr_observed)?x.pbr_observed:null,
          pbr_source:clean(x.pbr_source),flow_reference:x.flow_reference||null});
      }else{
        if(raw||x.published_price!==null)return null;
        rows.push({code:x.code,name:x.name,market:x.market,sector:x.sector||null,
          price:null,price_history_status:x.price_history_status,
          reference_market_cap_eok:fin(x.reference_market_cap_eok)?x.reference_market_cap_eok:null,
          reference_trading_value_eok:fin(x.reference_trading_value_eok)?x.reference_trading_value_eok:null,
          listing_zero_volume_reference:zeroVolumeReference,
          pbr_observed:fin(x.pbr_observed)?x.pbr_observed:null,
          pbr_source:clean(x.pbr_source),flow_reference:null,
          patterns:[],signals:[],flows_confirmed:false});
      }
    }
    if(counts.priced!==view.stocks.length||existing.size!==counts.priced)return null;
    for(const [key,v] of Object.entries(counts))if(v!==catalog.status_counts?.[key])return null;
    return {...view,stocks:rows,total:rows.length,catalogLoaded:true,
      pricedCount:counts.priced,missingCount:rows.length-counts.priced};
  }
  function mergePartial(view,partial){
    if(!view?.catalogLoaded||!partial||
        partial.schema!=='v22-current-listed-partial-daily-60to159-v0.1'||
        partial.asof!==view.date||!Array.isArray(partial.rows)||
        partial.rows.length>80||
        partial.rows.length!==partial.listed_insufficient_history_count||
        partial.full_v22_score_allowed!==false||
        partial.v3_validated_technical_score_allowed!==false||
        partial.confirmed_buy_signal!==false||
        partial.raw_ohlcv_published!==false)return null;
    const candidates=new Map(view.stocks.filter(r=>r.price_history_status==='insufficient_history')
      .map(r=>[r.code,r]));
    if(candidates.size!==partial.rows.length)return null;
    const seen=new Set(),map=new Map(),outcomes={partial_technical_observed:0,not_observable_partial:0};
    for(const x of partial.rows){
      if(!x||!ID.test(clean(x.code))||!candidates.has(x.code)||seen.has(x.code)||
         !Object.prototype.hasOwnProperty.call(outcomes,x.status)||
         !Number.isInteger(x.bars)||x.bars<0||x.bars>3500)return null;
      seen.add(x.code);outcomes[x.status]++;
      if(x.status==='partial_technical_observed'){
        if(x.bars<60||x.bars>=160||!fin(x.close_reference)||x.close_reference<=0||
           !fin(x.ma20)||x.ma20<=0||!fin(x.ma60)||x.ma60<=0||
           !fin(x.close_vs_ma20_pct)||!fin(x.close_vs_ma60_pct)||
           !['rising','falling','flat'].includes(x.obv_direction_60_daily)||
           !(x.ma120_daily===null||(x.bars>=120&&fin(x.ma120_daily)&&x.ma120_daily>0))||
           (x.bars<120&&x.ma120_daily!==null)||
           !(x.volume_ratio_20_vs_prior_20===null||
             (fin(x.volume_ratio_20_vs_prior_20)&&x.volume_ratio_20_vs_prior_20>=0))||
           x.full_score!==undefined||x.stop_line!==undefined||
           x.v3_buy_signal!==undefined)return null;
      }else if(x.close_reference!==undefined||x.ma20!==undefined||
               x.ma60!==undefined)return null;
      map.set(x.code,x);
    }
    if(seen.size!==candidates.size||
       outcomes.partial_technical_observed!==partial.partial_observed_count||
       outcomes.not_observable_partial!==partial.still_unavailable_count)return null;
    return {...view,stocks:view.stocks.map(r=>map.has(r.code)?
      {...r,partialObservation:map.get(r.code)}:r),
      partialLoaded:true,partialObservedCount:outcomes.partial_technical_observed};
  }
  function priceRisk(row) {
    const stop=n(row&&row.stop_line), price=n(row&&row.price);
    if(!(price>0&&stop>0&&stop<price))return null;
    const risk=(price-stop)/price*100;
    // Original producer's stop_pct is negative. A contradictory value
    // cannot be advertised as reliably 'low risk'.
    if(fin(row.stop_pct)&&Math.abs(Number(row.stop_pct)+risk)>2)return null;
    return +risk.toFixed(2);
  }
  function riskDescription(r){
    const p=priceRisk(r);
    if(p===null)return '손절 참고선 미확인';
    if(p>15)return '하락 위험 부담 큼';
    if(p<3)return '기준선 근접·노이즈 위험';
    return '구조 참고선 관찰';
  }
  function early(r) {
    return /초기|전환|수렴|바닥/.test(clean(r.phase));
  }
  function eligible(r,filter) {
    if(filter.market&&filter.market!=='all'&&filter.market!==r.market)return false;
    if(filter.status==='priced'&&r.price_history_status&&r.price_history_status!=='priced')return false;
    if(filter.status==='missing'&&(!r.price_history_status||r.price_history_status==='priced'))return false;
    if(filter.kind==='early'&&!early(r))return false;
    if(filter.kind==='partial'&&r.partialObservation?.status!=='partial_technical_observed')return false;
    if(filter.kind==='weekly'&&!((r.signals||[]).includes('주봉 120주선 회복')||
      (fin(r.ma120w_gap)&&Math.abs(r.ma120w_gap)<=10)))return false;
    if(filter.kind==='pattern'&&!(Array.isArray(r.patterns)&&r.patterns.length))return false;
    if(filter.kind==='volume'&&!(r.one_day_volume_ratio>=1.5||r.volume_ratio>=1.4))return false;
    if(filter.kind==='obv'&&r.obv_rising!==true)return false;
    const risk=priceRisk(r);
    if(filter.risk==='controlled'&&!(risk!==null&&risk>=3&&risk<=15))return false;
    if(filter.risk==='unverified'&&risk!==null)return false;
    const needle=clean(filter.search).trim().toLocaleLowerCase('ko-KR');
    if(needle&&!(r.code+' '+r.name+' '+(r.sector||'')).toLocaleLowerCase('ko-KR').includes(needle))return false;
    return true;
  }
  function rankRows(rows) {
    return rows.slice().sort((a,b)=>{
      const am=a.price_history_status&&a.price_history_status!=='priced';
      const bm=b.price_history_status&&b.price_history_status!=='priced';
      if(am!==bm)return am?1:-1;
      const ar=priceRisk(a),br=priceRisk(b);
      if(ar!==null&&br===null)return -1;
      if(ar===null&&br!==null)return 1;
      if(ar!==null&&br!==null&&ar!==br)return ar-br;
      return a.code.localeCompare(b.code);
    });
  }
  function isStale(day,now=Date.now()){
    if(!validDay(day))return true;
    const age=(now-new Date(day+'T18:30:00+09:00').getTime())/86400000;
    return age>5||age< -1;
  }
  function safeRecord(x) {
    if (!x||typeof x!=='object'||!ID.test(clean(x.code))||
        !validDay(x.date)||!fin(x.entry)||x.entry<=0||
        (x.stop!==null&&!(fin(x.stop)&&x.stop>0&&x.stop<x.entry)))return null;
    return {
      id:x.date+'|'+x.code, date:x.date, code:x.code,
      name:clean(x.name).slice(0,70),
      entry:x.entry, stop:x.stop, source:'v2_public_price_snapshot'
    };
  }
  function makeRecord(r,day){
    if(!validDay(day)||!r||!ID.test(clean(r.code))||!fin(r.price)||r.price<=0||
       r.listing_zero_volume_reference===true) return null;
    const stop=priceRisk(r)!==null?r.stop_line:null;
    return safeRecord({date:day,code:r.code,name:r.name,entry:r.price,stop});
  }
  function markToMarket(record,latest,day,now=Date.now()){
    const q=safeRecord(record);
    if(!q||isStale(day,now)||day<q.date)return {state:'no_fresh_quote',gainPct:null,stopCloseBroken:null};
    const r=latest&&latest[q.code];
    if(!r||!fin(r.price)||r.price<=0)return {state:'no_fresh_quote',gainPct:null,stopCloseBroken:null};
    return {
      state:day===q.date?'entry_day':'observed_latest_close',
      gainPct:+((r.price/q.entry-1)*100).toFixed(2),
      stopCloseBroken:q.stop===null?null:r.price<=q.stop,
      latestPrice:r.price,asof:day,
    };
  }
  const core={parseSnapshot,mergeCatalog,mergePartial,priceRisk,riskDescription,eligible,rankRows,isStale,safeRecord,makeRecord,markToMarket,escapeHtml,KIND_VALUES,RISK_VALUES,STATUS_VALUES,MARKET_VALUES};
  if(typeof module!=='undefined'&&module.exports)module.exports=core;
  root.V3BetaCore=core;
  if(!root.document)return;

  function init(){
    const D=root.document,by=(id)=>D.getElementById(id);
    let view=null,records=[],limit=40;
    const msgs=by('v3Status'),list=by('v3List'),ledger=by('v3Ledger');
    const safeStore={
      load(){
        try{
          const raw=JSON.parse(root.localStorage.getItem(STORAGE_KEY)||'[]');
          if(!Array.isArray(raw))return [];
          return raw.slice(0,250).map(safeRecord).filter(Boolean)
            .filter((item,i,all)=>all.findIndex(z=>z.id===item.id)===i);
        }catch(_){return [];}
      },
      save(){
        try{root.localStorage.setItem(STORAGE_KEY,JSON.stringify(records));return true;}
        catch(_){msgs.textContent='로컬 저장 불가: 브라우저 보안 또는 용량 설정 확인';return false;}
      }
    };
    records=safeStore.load();
    function filters(){return {
      search:by('v3Search').value,kind:by('v3Pattern').value,risk:by('v3Risk').value,
      market:by('v3Market').value,status:by('v3Coverage').value
    };}
    function rowCard(r){
      const missing=r.price_history_status&&r.price_history_status!=='priced';
      const cap=fin(r.reference_market_cap_eok)?r.reference_market_cap_eok:r.market_cap_eok;
      const trade=fin(r.reference_trading_value_eok)?r.reference_trading_value_eok:r.trading_eok;
      const pbr=fin(r.pbr_observed)?r.pbr_observed:r.pbr;
      const sourceNote=fin(pbr)?'PBR '+pbr.toFixed(2)+'배 (출처: '+clean(r.pbr_source||'v2 공개 종목자료')+' · 추가 검증 전)':'PBR 미확인';
      const firm='시가총액 '+(fin(cap)?cap.toLocaleString('ko-KR')+'억원':'미확인')+
        ' · 거래대금 '+(fin(trade)?trade.toLocaleString('ko-KR')+'억원':'미확인');
      if(missing){
        const reason={insufficient_history:'가격 이력 부족',price_source_or_validation_error:'가격 수집·검증 오류',
          unattempted_batch:'해당 배치 미시도'}[r.price_history_status]||'가격 분석 불가';
        // The listing's 0.00억원 is rounded (can include small positive
        // transactions), so never claim confirmed suspension or exact zero.
        const lowTurnoverNote=r.listing_zero_volume_reference===true?
          ' · 상장 목록 참고 거래량 0주. 별도 시장·정규장 검증 전이므로 거래정지로 단정하지 않으며 매수 검토 제외.':
          r.price_history_status==='price_source_or_validation_error'&&trade===0?
          (r.listing_zero_volume_reference===false?
            ' · 상장 목록에는 거래량이 있으나 거래대금이 0.00억원으로 반올림됨. 정확한 거래금액 및 매매 적합성 미확인.':
            ' · 상장 목록 참고 거래대금 0.00억원(반올림). 원 단위 무거래·거래정지 여부는 미확인.'):'';

        const partial=r.partialObservation?.status==='partial_technical_observed'?
          '<p class="note"><b>일봉 부분 관측 ('+r.partialObservation.bars+'개)</b> · 기준일 종가 '+
          money(r.partialObservation.close_reference)+' · 20일 MA '+
          money(r.partialObservation.ma20)+' · 60일 MA '+
          money(r.partialObservation.ma60)+
          (fin(r.partialObservation.ma120_daily)?' · 일봉 120일 MA '+
            money(r.partialObservation.ma120_daily):' · 일봉 120일 MA 미확보')+
          ' · OBV 60일 방향 '+escapeHtml(r.partialObservation.obv_direction_60_daily)+
          ' · 지난 20일/직전 20일 거래량 비율 '+
          (fin(r.partialObservation.volume_ratio_20_vs_prior_20)?
            r.partialObservation.volume_ratio_20_vs_prior_20.toFixed(2)+'배':'미확인')+
          '. <b>주봉120주선·정식 점수·매수 판단 없음</b></p>':'';
        return '<article class="v3-stock"><div class="v3-head"><div><strong>'+escapeHtml(r.name)+
          '</strong><div class="sub">'+escapeHtml(r.code)+' · '+escapeHtml(r.market)+
          '</div></div><span class="v3-tag">'+escapeHtml(reason)+'</span></div>'+
          '<p class="sub">'+escapeHtml(firm)+' · '+escapeHtml(sourceNote)+'</p>'+
          '<p class="note">현재 분석 기준일의 기술 지표가 없어 모의 진입 기록과 매수 판단을 생성하지 않습니다.'+
          escapeHtml(lowTurnoverNote)+'</p>'+partial+'</article>';
      }
      const risk=priceRisk(r);
      const signals=Array.isArray(r.patterns)?r.patterns.slice(0,2).map(escapeHtml).join(' · '):'없음';
      const stop=risk!==null?money(r.stop_line)+' (현재가 대비 -'+risk.toFixed(1)+'%)':'구조 참고선 미확인';
      const price20=pct(n(r.price20));
      const price60=pct(n(r.price60));
      const volume=fin(r.one_day_volume_ratio)?r.one_day_volume_ratio.toFixed(2)+'배':'미확인';
      const weekly=fin(r.ma120w_gap)?pct(r.ma120w_gap):'미확인';
      const old=records.some(q=>q.id===view.date+'|'+r.code);
      return '<article class="v3-stock">'+
        '<div class="v3-head"><div><strong>'+escapeHtml(r.name)+'</strong>'+
        '<div class="sub">'+escapeHtml(r.code)+' · '+escapeHtml(r.market||'시장 미확인')+
        ' · '+escapeHtml(r.sector||'업종 미확인')+'</div></div>'+
        '<span class="v3-tag">'+escapeHtml(r.phase||'위치 미확인')+'</span></div>'+
        '<div class="v3-metrics">'+
        '<span><small>기준일 종가</small><b>'+money(r.price)+'</b></span>'+
        '<span><small>구조 참고 손절선</small><b>'+stop+'</b></span>'+
        '<span><small>20일 / 60일 주가</small><b>'+price20+' / '+price60+'</b></span>'+
        '<span><small>당일 거래량 / 주봉120주선 괴리</small><b>'+volume+' / '+weekly+'</b></span>'+
        '</div><p class="sub">'+escapeHtml(firm)+' · '+escapeHtml(sourceNote)+'</p><p class="sub">관측 패턴: '+(signals||'없음')+' · OBV 상승 후보: '+
        (r.obv_rising===true?'있음':'확인되지 않음')+
        ' · 수급: '+(r.flow_reference&&Number.isInteger(r.flow_reference.foreign_20d_shares_observed)&&
          Number.isInteger(r.flow_reference.institution_20d_shares_observed)?
          '외국인 '+r.flow_reference.foreign_20d_shares_observed.toLocaleString('ko-KR')+
          '주 / 기관 '+r.flow_reference.institution_20d_shares_observed.toLocaleString('ko-KR')+
          '주 ('+escapeHtml(r.flow_reference.end||'기간 미확인')+' 기준 · 출처: '+escapeHtml(r.flow_reference.source||'기준 미확인')+' · 참고자료)':
          r.flows_confirmed===true?'v2 관측 있음 (독립 검증 전)':'미확인')+'</p>'+
        '<div class="v3-bottom"><span>'+escapeHtml(riskDescription(r))+
        (Array.isArray(r.risk_flags)&&r.risk_flags.length?' · '+escapeHtml(r.risk_flags.slice(0,2).join(' · ')):'')+
        '</span><button type="button" data-capture="'+escapeHtml(r.code)+'"'+
        (old?' disabled':'')+'>'+(old?'기록됨':'모의 관찰 기록')+'</button></div>'+
        '</article>';
    }
    function render(){
      if(!view){list.textContent='가격 자료 확인 중…';return;}
      const f=filters();
      if(!KIND_VALUES.has(f.kind)||!RISK_VALUES.has(f.risk)||
         !STATUS_VALUES.has(f.status)||!MARKET_VALUES.has(f.market))return;
      const matching=rankRows(view.stocks.filter(r=>eligible(r,f)));
      const selected=matching.slice(0,limit);
      by('v3Count').textContent='현재 상장 종목 '+view.stocks.length.toLocaleString('ko-KR')+
        '개 ('+(view.catalogLoaded?'전 종목 목록 연결':'가격 관측 가능한 종목만 연결')+') · 가격 분석 '+
        (view.pricedCount??view.stocks.length).toLocaleString('ko-KR')+'개 · 자료 부족/오류 '+
        (view.missingCount??'미집계')+'개 · 일봉 부분 관측 '+
        (view.partialLoaded?view.partialObservedCount+'개':'게시 대기')+
        ' · 필터 '+matching.length.toLocaleString('ko-KR')+
        '개 · '+selected.length+'개 표시';
      list.innerHTML=selected.length?selected.map(rowCard).join(''):'<p class="empty">현재 조건을 충족하는 후보가 없어 관망합니다. 필터를 바꿔 확인할 수 있습니다.</p>';
      by('v3More').hidden=matching.length<=limit;
    }
    function renderLedger(){
      if(!records.length){ledger.innerHTML='<p class="empty">아직 기록한 관찰 후보가 없습니다. 종목을 검색하고 ‘모의 관찰 기록’을 누르세요.</p>';return;}
      const index=Object.fromEntries((view?.stocks||[]).map(r=>[r.code,r]));
      ledger.innerHTML=records.slice().reverse().map(r=>{
        const v=markToMarket(r,index,view?.date||'',Date.now());
        const result=v.gainPct===null?'후속 종가 미확인':pct(v.gainPct);
        const stopText=r.stop===null?'기준선 없음':money(r.stop);
        const label=v.stopCloseBroken===true?'최근 관측 종가가 무효화 참고선 이하':v.stopCloseBroken===false?'최근 종가 기준 참고선 위':'손절선 상태 미확인';
        return '<div class="v3-paper"><div><b>'+escapeHtml(r.name)+'</b> <small>'+
          escapeHtml(r.code)+' · 기록 '+escapeHtml(r.date)+'</small></div>'+
          '<div class="v3-metrics"><span><small>기록 종가</small><b>'+money(r.entry)+'</b></span>'+
          '<span><small>당시 손절 참고선</small><b>'+stopText+'</b></span>'+
          '<span><small>최근 공개 종가 대비 변동</small><b>'+result+'</b></span></div>'+
          '<div class="v3-bottom"><span>'+escapeHtml(label)+'</span>'+
          '<button type="button" data-delete="'+escapeHtml(r.id)+'">기록 삭제</button></div></div>';
      }).join('');
      by('v3LedgerNote').textContent='현재 '+records.length+'건 · 브라우저 기기 내부 저장. 최신 종가만 비교하며 중간 거래일의 저가·손절선 선도달·수익률 검증은 수행하지 않습니다.';
    }
    function noteStatus(){
      if(!view)return;
      const stale=isStale(view.date);
      msgs.textContent=stale
        ?'기준일 '+view.date+' · 데이터가 오래되어 신규 모의 관찰 기록을 중단했습니다.'
        :'기준일 '+view.date+' · 공개 v2.2 가격 관측값으로 v3 사용자 모의검증 진행 중';
      msgs.className=stale?'status error':'status';
      by('v3Notice').textContent='v3.0b의 거래량20·기술25·재무20·밸류에이션20·모멘텀15에 대한 정식 인증 점수·예측 수익률·실거래 추천은 아직 제공하지 않습니다. 산업은 정보용이며 100점 배점에 포함되지 않습니다. 패턴·거래량·주봉120주·손절 참고선은 기존 v2.2 출처의 잠정 관측이며 공식 KRX 10년 백테스트 인증이 아닙니다.';
    }
    async function load(){
      try{
        msgs.textContent='공개 시장 가격 자료 확인 중…';
        const response=await root.fetch('data.json?beta='+Date.now(),{cache:'no-store'});
        if(!response.ok)throw Error('자료 요청 오류');
        const source=await response.json();
        view=parseSnapshot(source);
        // The complete active-listing index is a separately published derived
        // catalog. Do not blend stale or inconsistent sources into stock facts.
        try{
          const catalogResponse=await root.fetch('v22-market-catalog.json?beta='+Date.now(),{cache:'no-store'});
          if(catalogResponse.ok){
            const candidate=await catalogResponse.json();
            const merged=mergeCatalog(view,candidate);
            if(merged)view=merged;
          }
        }catch(_){/* Existing valid price cards remain usable until next market run. */}
        if(view.catalogLoaded){
          try{
            const partialResponse=await root.fetch('v22-partial-history.json?beta='+Date.now(),{cache:'no-store'});
            if(partialResponse.ok){
              const p=await partialResponse.json();
              const merged=mergePartial(view,p);
              if(merged)view=merged;
            }
          }catch(_){/* Missing or stale short-history optional data keeps existing safe rows. */}
        }
        noteStatus();
        render();renderLedger();
      }catch(_){
        view=null;
        msgs.textContent='공개 가격 데이터가 없거나 형식이 올바르지 않습니다. 새 신호를 만들지 않습니다.';
        msgs.className='status error';
        by('v3Count').textContent='자료 확인 실패';
        list.innerHTML='<p class="empty">분석자료를 가져오지 못해 후보를 표시하지 않습니다.</p>';
        renderLedger();
      }
    }
    list.addEventListener('click',event=>{
      const button=event.target.closest('[data-capture]');
      if(!button||!view||isStale(view.date))return;
      const stock=view.stocks.find(x=>x.code===button.dataset.capture);
      const v=makeRecord(stock,view.date);
      if(!v||records.some(x=>x.id===v.id))return;
      if(records.length>=250){msgs.textContent='모의 관찰 기록 250건 한도입니다. 먼저 내보내기 후 정리하세요.';return;}
      records.push(v);
      if(!safeStore.save()){records.pop();return;}
      render();renderLedger();
    });
    ledger.addEventListener('click',event=>{
      const button=event.target.closest('[data-delete]');
      if(!button)return;
      const old=records;records=records.filter(x=>x.id!==button.dataset.delete);
      if(!safeStore.save()){records=old;return;}
      render();renderLedger();
    });
    by('v3Export').addEventListener('click',()=>{
      if(!records.length)return;
      // JSON rather than CSV: avoid formula injection and permit offline audit.
      const payload={schema:STORAGE_KEY,exported_at_utc:new Date().toISOString(),
        disclaimer:'v2.2 public close observation, no verified intraday stops',
        observations:records};
      const blob=new root.Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
      const url=root.URL.createObjectURL(blob);
      const a=D.createElement('a');a.href=url;a.download='v3-paper-observations.json';
      a.click();root.URL.revokeObjectURL(url);
    });
    ['v3Search','v3Pattern','v3Risk','v3Market','v3Coverage'].forEach(id=>by(id).addEventListener(
      id==='v3Search'?'input':'change',()=>{limit=40;render();}));
    by('v3More').addEventListener('click',()=>{limit=Math.min(limit+40,400);render();});
    by('v3Reload').addEventListener('click',load);
    renderLedger();load();
  }
  root.document.addEventListener('DOMContentLoaded',init);
})(typeof window!=='undefined'?window:globalThis);
