/* v3 사용자 모의검증 베타. 기존 공개 v2.2 관측값을 활용하는 연구용 화면.
 * NO v3 six-pillar official score, NO observed future performance claim.
 * User's paper records reside in own browser localStorage only.
 */
(function(root) {
  'use strict';
  const STORAGE_KEY='kr-stock-v3-paper-beta-1';
  const ID=/^[0-9A-Z]{6}$/;
  const KIND_VALUES=new Set(['all','early','weekly','pattern','volume','obv']);
  const RISK_VALUES=new Set(['all','controlled','unverified']);
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
    if(filter.kind==='early'&&!early(r))return false;
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
    if(!validDay(day)||!r||!ID.test(clean(r.code))||!fin(r.price)||r.price<=0) return null;
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
  const core={parseSnapshot,priceRisk,riskDescription,eligible,rankRows,isStale,safeRecord,makeRecord,markToMarket,escapeHtml,KIND_VALUES,RISK_VALUES};
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
      search:by('v3Search').value,kind:by('v3Pattern').value,risk:by('v3Risk').value
    };}
    function rowCard(r){
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
        '</div><p class="sub">관측 패턴: '+(signals||'없음')+' · OBV 상승 후보: '+
        (r.obv_rising===true?'있음':'확인되지 않음')+
        ' · 외국인·기관 수급: '+(r.flows_confirmed===true?'v2 관측 있음 (공식 미인증)':'미확인')+'</p>'+
        '<div class="v3-bottom"><span>'+escapeHtml(riskDescription(r))+
        (Array.isArray(r.risk_flags)&&r.risk_flags.length?' · '+escapeHtml(r.risk_flags.slice(0,2).join(' · ')):'')+
        '</span><button type="button" data-capture="'+escapeHtml(r.code)+'"'+
        (old?' disabled':'')+'>'+(old?'기록됨':'모의 관찰 기록')+'</button></div>'+
        '</article>';
    }
    function render(){
      if(!view){list.textContent='가격 자료 확인 중…';return;}
      const f=filters();
      if(!KIND_VALUES.has(f.kind)||!RISK_VALUES.has(f.risk))return;
      const matching=rankRows(view.stocks.filter(r=>eligible(r,f)));
      const selected=matching.slice(0,limit);
      by('v3Count').textContent='전체 분석 가능 '+view.stocks.length.toLocaleString('ko-KR')+
        '종목 · 필터 '+matching.length.toLocaleString('ko-KR')+'종목 · '+selected.length+'종목 표시';
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
      by('v3Notice').textContent='v3 확정 6영역 점수·예측 수익률·실거래 추천은 아직 제공하지 않습니다. 패턴·거래량·주봉120주·손절 참고선은 기존 v2.2 출처의 잠정 관측이며 공식 KRX 10년 백테스트 인증이 아닙니다.';
    }
    async function load(){
      try{
        msgs.textContent='공개 시장 가격 자료 확인 중…';
        const response=await root.fetch('data.json?beta='+Date.now(),{cache:'no-store'});
        if(!response.ok)throw Error('자료 요청 오류');
        const source=await response.json();
        view=parseSnapshot(source);
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
    ['v3Search','v3Pattern','v3Risk'].forEach(id=>by(id).addEventListener(
      id==='v3Search'?'input':'change',()=>{limit=40;render();}));
    by('v3More').addEventListener('click',()=>{limit=Math.min(limit+40,400);render();});
    by('v3Reload').addEventListener('click',load);
    renderLedger();load();
  }
  root.document.addEventListener('DOMContentLoaded',init);
})(typeof window!=='undefined'?window:globalThis);
