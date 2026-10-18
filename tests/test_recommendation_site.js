'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const source=html.slice(html.lastIndexOf('<script>')+8,html.lastIndexOf('</script>'));
assert.doesNotThrow(()=>new vm.Script(source,{filename:'docs/index.html inline JS'}));

for(const marker of [
 'id="deepResearch"','id="deepResearchStatus"','id="deepChanges"',
 'id="deepDossiers"','전체시장 기반 종목별 심층 분석',
 '기술점수 상위 20개 · 추가 탐색용',
 "deepReport.asof!==marketSnapshot.date",
 'research_eligible_count','confirmed_recommendation_count',
 "d.daily_change","d.evidence_gaps","d.scenarios?.down?.conditions",
 '미래 기대수익률','미래 수익률 및 확률: 미산출'
]){
 assert.ok(html.includes(marker),'required deep report: '+marker);
}
assert.ok(html.indexOf('id="deepResearch"')<
          html.indexOf('id="practicalWatchlist"'));
assert.ok(html.indexOf('id="practicalWatchlist"')<
          html.indexOf('id="screenList"'));
assert.ok(!html.includes('추천 20종목 확정'));
const ui=fs.readFileSync(path.join(__dirname,'../docs/stock_research_ui.js'),'utf8');
assert.ok(ui.includes("elem('deepDossiers').addEventListener('click',onQuantClick)"));

function fixture(date='2026-10-16') {
 return {
  schema:'recommendations-v1-research',asof:date,
  screened_count:2499,market_scan_complete:true,
  research_eligible_count:1,research_dossier_count:1,
  confirmed_recommendation_count:0,
  daily_changes:{entered:['123456'],updated:[],exited:['555555'],unchanged:[]},
  dossiers:[{
   code:'123456',name:'가상기업',rank:1,price:10000,daily_change:'entered',
   confirmed_buy:false,trade_action:'추가 검증 전 관망',
   approach_horizon:'1m',approach_basis:'일봉 초기 돌파',
   long_holding_caution:false,
   v2_score:{normalized:85.7,available:70},
   downside:{risk_level:'low',stop:{stop_pct:-6},
     structural_invalidation_price:9400,
     hypothesis:'지지선 이탈 시 재검토'},
   return_assessment:{expected_gain_pct:null,expected_reward_risk:null},
   technical:{phase:'초기 돌파 확인 중',signals:['거래량 동반'],
     obv_rising:true},
   flows:{validated:false,comment:'수급 미확인'},
   financials:{validated:false,comment:'재무 미확인'},
   industry:{validated:false,comment:'업황 미확인'},
   valuation:{validated:false,comment:'기업가치 미확인',pbr:null},
   historical_quant:{
     '1m':{evidence:'missing_or_insufficient'},
     '3m':{evidence:'missing_or_insufficient'},
     '6m':{evidence:'missing_or_insufficient'},
     '12m':{evidence:'missing_or_insufficient'}
   },
   scenarios:{up:{conditions:'거래량 증가'},neutral:{conditions:'횡보'},
     down:{conditions:'지지선 이탈'}},
   evidence_gaps:['재무','수급','업황']
  }]
 };
}

function browser(returnDate){
 const elements=new Map();
 function byId(id){
   if(!elements.has(id))elements.set(id,{
    id,style:{},value:'all',innerHTML:'',textContent:'',
    className:'',href:'',events:{},children:[],
    addEventListener(k,h){this.events[k]=h;},
    setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k];},
    replaceChildren(){this.children=[];},
    appendChild(x){this.children.push(x);}
   });
   return elements.get(id);
 }
 byId('sort').value='score';
 const stock={
   code:'123456',name:'가상기업',price:10000,
   components:{'기술':70,'재무':null},phase:'초기 돌파 확인 중'
 };
 const snapshot={
  status:'success',version:'2.0',date:'2026-10-16',
  weights:{'기술':70,'재무':30},stocks:[stock],
  candidate_count:1,examined_count:1,eligible_count:1,
  history_deferred_count:0,error_count:0,
  history_exclusions:[],flow_confirmed_count:0,fundamental_count:0,
  index_available:[],generated_kst:'2026-10-16 19:00 KST'
 };
 const replies={
  'data.json':snapshot,
  'recommendations.json':fixture(returnDate),
  'quant.json':{asof:'2026-10-16',stocks:[]},
  'history.json':[]
 };
 const requests=[];
 const context={
   window:{
    PartialScoreView:{rating:()=>({normalized:85.7,available:70,
     earned:60,coverage:70,missing:[]})},
    StockResearchUI:{mount:()=>({setMarket(){},refreshQuality(){}})},
    V22Watchlist:null
   },
   document:{getElementById:byId,
      createElement:()=>({textContent:'',href:'',download:''}),
      querySelectorAll:()=>[]},
   Date,Math,Number,String,JSON,Array,
   fetch:async url=>{
    requests.push(url);
    const k=Object.keys(replies).find(k=>url.startsWith(k));
    if(!k)return {ok:false,status:404,json:async()=>null};
    return {ok:true,json:async()=>replies[k]};
   }
 };
 vm.runInNewContext(source,context,{filename:'inline.js'});
 return {elements,requests,byId};
}
async function settle(){
 for(let i=0;i<25;i++)await Promise.resolve();
}
(async()=>{
 const same=browser('2026-10-16');
 await settle();
 assert.ok(same.byId('deepResearchStatus').textContent.includes('2,499'));
 assert.ok(same.byId('deepResearchStatus').textContent.includes('확정 매수 추천 0종목'));
 assert.ok(same.byId('deepDossiers').innerHTML.includes('심층 분석 진행 · 매수 보류'));
 assert.ok(same.byId('deepDossiers').innerHTML.includes('미래 기대수익률 미산출'));
 assert.ok(same.byId('deepDossiers').innerHTML.includes('재무 미확인'));
 assert.ok(same.byId('deepDossiers').innerHTML.includes('9,400'));
 assert.ok(same.byId('deepChanges').textContent.includes('123456'));
 assert.ok(same.byId('deepChanges').textContent.includes('555555'));
 assert.ok(same.requests.some(u=>u.startsWith('recommendations.json')));

 const stale=browser('2026-10-15');
 await settle();
 assert.ok(stale.byId('deepResearchStatus').textContent.includes('아직 없습니다'));
 assert.ok(stale.byId('deepDossiers').innerHTML.includes('매수 추천 없음'));
 assert.ok(!stale.byId('deepDossiers').innerHTML.includes('심층 분석 진행 · 매수 보류'));
 console.log('dynamic_recommendation_home_offline_tests_passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
