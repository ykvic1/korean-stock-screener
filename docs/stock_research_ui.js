/* Browser integration for already computed, limited-sample v2.1/v2.2 studies.
 * Read-only. No GitHub token, background workflow dispatch or fake new audit.
 */
(function (root) {
  'use strict';
  const h = x => String(x ?? '').replace(/[&<>"']/g,c=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
  const numeric = x => typeof x==='number' && Number.isFinite(x);
  const number = (x,suffix='') => numeric(x)?h(String(x)+suffix):'미검증';
  const elem = x => document.getElementById(x);

  function mount({getStocks,getMarket,redraw}) {
    let audit=null, qualitySeq=0, quantCache=null, activeCode=null;

    function assignQuality() {
      const snapshot=getMarket();
      if(!snapshot || !Array.isArray(snapshot.stocks)) return;
      for(const stock of snapshot.stocks) {
        stock._quality=root.StockResearch?.quality(stock.code,audit,snapshot.date)||null;
      }
      redraw();
    }

    function refreshQuality() {
      const seq=++qualitySeq;
      const banner=elem('qualityStatus');
      if(banner)banner.textContent='종목별 데이터 품질 연구를 자동으로 불러오고 있습니다.';
      return fetch('reliability.json?t='+Date.now(),{cache:'no-store'}).then(res=>{
        if(!res.ok) throw Error('quality_http_failed');
        return res.json();
      }).then(source=>{
        if(seq!==qualitySeq)return;
        if(!source||!Array.isArray(source.stocks)||typeof source.asof!=='string')
          throw Error('invalid_quality_payload');
        audit=source;
        const snapshot=getMarket();
        const same=source.asof===snapshot?.date;
        if(banner)banner.textContent='가격 신뢰성 연구 '+source.asof+' 기준 · '+
          source.stocks.length+'종목 표본 · 전시장 전체의 품질 검증은 아님'+
          (same?' · 종목별 결과 자동 표시':' · 현재 가격 기준일과 다르므로 과거 연구로 표시');
        assignQuality();
      }).catch(()=>{
        if(seq!==qualitySeq)return;
        audit=null;
        if(banner)banner.textContent='신뢰성 연구파일 이용 불가. 가격 분석은 계속 표시하되 교차 검증은 미확인입니다.';
        assignQuality();
      });
    }

    function setMarket(snapshot) {
      for(const row of snapshot.stocks||[]) {
        row._quality=root.StockResearch?.quality(row.code,audit,snapshot.date)||null;
      }
    }

    function showMessage(title,context,body) {
      elem('quantTitle').textContent=title;
      elem('quantContext').textContent=context;
      elem('quantResults').textContent=body;
      elem('quantPanel').hidden=false;
    }

    async function openQuant(code) {
      if(!/^\d{6}$/.test(code))return;
      const row=getStocks().find(r=>r.code===code);
      if(!row)return;
      activeCode=code;
      showMessage(row.name+' ('+code+') · 퀀트 검증',
        '저장된 과거 연구 결과를 조회합니다. 클릭만으로 신규 백테스트가 실행되는 것은 아닙니다.',
        '해당 종목의 퀀트 연구 결과 확인 중…');
      elem('quantPanel').scrollIntoView({behavior:'smooth',block:'start'});
      try {
        if(!quantCache) {
          quantCache=fetch('quant.json?t='+Date.now(),{cache:'no-store'}).then(res=>{
            if(!res.ok)throw Error('quant_http_failed');
            return res.json();
          }).catch(e=>{quantCache=null;throw e;});
        }
        const source=await quantCache;
        if(activeCode!==code || elem('quantPanel').hidden)return;
        const result=root.StockResearch?.quant(code,source,getMarket()?.date);
        if(!result){elem('quantResults').textContent='퀀트 연구 자료를 해석할 수 없습니다.';return;}
        elem('quantContext').textContent='과거 연구 '+(result.asof||'미확인')+
          ' 기준 · 표본 '+(result.sampled??'미확인')+'종목 · '+result.label;
        if(result.state==='not-studied'||result.state==='unavailable'||
           result.state==='invalid-code'){
          elem('quantResults').innerHTML='<p class="research-warn">'+h(result.label)+
            '</p><p class="research-note">이 종목의 과거 퀀트 검증 결과가 저장돼 있지 않습니다. 매매 부적격이나 기업 부실이라는 의미가 아니며, 향후 자동 연구 범위를 확대해야 합니다.</p>';
          return;
        }
        const rows=result.rows.map(p=>{
          if(p.status!=='exploratory'){
            return '<tr><td>'+h(p.period)+'</td><td>'+number(p.sample_n)+
              '</td><td colspan="4" class="research-warn">'+h(p.reason)+'</td></tr>';
          }
          return '<tr><td>'+h(p.period)+'</td><td>'+number(p.sample_n)+
            '</td><td>'+number(p.up_pct,'%')+'</td><td>'+
            number(p.mean_net_return_pct,'%')+'</td><td>'+
            number(p.stop_first_pct,'%')+'</td><td>'+
            number(p.max_adverse_p75_pct,'%')+'</td></tr>';
        }).join('');
        elem('quantResults').innerHTML=
          '<p class="research-note">유효한 과거 관측 기간 '+result.explored+
          '개 · 원래의 접근 구분: '+h(result.suggestion)+
          ' · 미래 상승확률 또는 현재 투자판단을 입증하지 않음</p>'+
          '<div class="quant-table-wrap"><table class="quant-table"><thead><tr>'+
          '<th>연구 기간</th><th>표본</th><th>상승 빈도</th><th>평균 순수익</th>'+
          '<th>손절 선도달</th><th>최대하락폭 75%</th>'+
          '</tr></thead><tbody>'+rows+'</tbody></table></div>';
      } catch(e) {
        if(activeCode!==code)return;
        elem('quantContext').textContent='퀀트 연구자료 조회 실패';
        elem('quantResults').textContent='데이터 파일을 읽지 못했습니다. 종목은 정상 표시하지만 퀀트 검증은 미확인으로 남깁니다.';
      }
    }

    function onQuantClick(e) {
      const btn=e.target.closest('[data-quant-code]');
      if(btn)openQuant(btn.dataset.quantCode);
    }
    elem('list').addEventListener('click',onQuantClick);
    elem('practicalList').addEventListener('click',onQuantClick);
    elem('closeQuant').addEventListener('click',()=>{
      activeCode=null;
      elem('quantPanel').hidden=true;
    });
    return {setMarket,refreshQuality,openQuant};
  }
  root.StockResearchUI={mount};
})(typeof window==='undefined'?{}:window);
