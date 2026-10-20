// The historic v2.2 UI remains fully regression-tested on legacy.html;
// the homepage now directly serves the unified V3 workspace.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const model=require('../docs/stock_research.js');
const date='2026-10-16';
const code='123456';
const entry={
 code,name:'가상종목',
 period_suggestion:{decision:'관망/추가 검증'},
 periods:{
  '스윙 1개월':{status:'exploratory',sample_n:66,up_pct:64,
    mean_net_return_pct:3.5,stop_first_pct:19.5,max_adverse_p75_pct:-7.2},
  '스윙 2개월':{status:'insufficient',sample_n:4,reason:'표본 부족',
    up_pct:99,mean_net_return_pct:80}
 }
};
const quantSource={asof:date,stocks:[entry]};
const qualityRecord={
 code,strict_valid:true,independently_matched:true,
 cross_validation:{status:'matched_recent'},
 usable_segment_rows:810,quarantined_rows:13,
 cause_categories:['OHLC_rounding_candidate']
};
const qualitySource={asof:date,stocks:[qualityRecord]};
assert.equal(model.quality(code,qualitySource,date).state,'matched');
assert.equal(model.quality(code,qualitySource,date).usable,810);
assert.equal(model.quality(code,qualitySource,date).official_session_verified,false);
assert.equal(model.quality('654321',qualitySource,date).state,'not-studied');
assert.equal(model.quality(code,qualitySource,'2026-10-17').state,'outdated');
assert.equal(model.quality(code,null,date).state,'unavailable');
assert.equal(model.quality(code,{asof:date,stocks:[{...qualityRecord,strict_valid:false}]},date).state,'needs-review');
assert.equal(model.quality(code,{asof:date,stocks:[{...qualityRecord,
 cross_validation:{status:'unconfirmed'}}]},date).state,'partial');
assert.equal(model.quality('wrong',qualitySource,date).state,'invalid-code');

const q=model.quant(code,quantSource,date);
assert.equal(q.state,'historical-observations');
assert.equal(q.rows.length,7);
assert.equal(q.explored,1);
assert.equal(q.predictive_probability,false);
assert.equal(q.rows.find(x=>x.period==='스윙 1개월').sample_n,66);
assert.equal(q.rows.find(x=>x.period==='스윙 2개월').up_pct,null);
assert.equal(q.rows.find(x=>x.period==='스윙 2개월').status,'insufficient');
assert.equal(model.quant('654321',quantSource,date).state,'not-studied');
assert.equal(model.quant(code,quantSource,'2026-10-17').state,'outdated');
assert.equal(model.quant(code,null,date).state,'unavailable');

const html=fs.readFileSync(path.join(__dirname,'../docs/legacy.html'),'utf8');
const order=['id="practicalWatchlist"','id="screenList"','id="quantPanel"',
 'id="marketSummary"','최근 엑셀 보고서','patch-notes.html'];
let previous=-1;
for(const marker of order){
 const n=html.indexOf(marker);
 assert.ok(n>previous,'investor-first hierarchy '+marker);
 previous=n;
}
assert.ok(html.includes('data-quant-code="'+'$'+'{esc(r.code)}"'));
assert.ok(html.includes('자동 가격품질 연구:'));
assert.ok(html.includes('stock_research.js'));
assert.ok(html.includes('stock_research_ui.js'));
assert.ok(html.includes('studies.setMarket(d)'));
assert.ok(html.includes('studies.refreshQuality()'));
const notes=fs.readFileSync(path.join(__dirname,'../docs/patch-notes.html'),'utf8');
for(const target of ['v2.1','v2.2','quant.html','reliability.html']){
 assert.ok(notes.includes(target));
}
assert.ok(!html.slice(html.indexOf('<main>'),html.indexOf('id="marketSummary"'))
 .includes('데이터 신뢰성 및 RSI 연구 열기'));

const elements=new Map();
function byId(id){
 if(!elements.has(id))elements.set(id,{
  hidden:true,innerHTML:'',textContent:'',events:{},
  addEventListener(type,handler){this.events[type]=handler;},
  scrollIntoView(){}
 });
 return elements.get(id);
}
const requests=[];
const market={date,stocks:[{code,name:'가상종목'},{code:'654321',name:'표본외'}]};
const context={
 window:{StockResearch:model},
 document:{getElementById:byId},
 Date,
 fetch:async(url,opts)=>{
  requests.push({url,opts});
  return {ok:true,json:async()=>url.startsWith('reliability.json')?qualitySource:quantSource};
 }
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../docs/stock_research_ui.js'),'utf8'),
 context,{filename:'stock_research_ui.js'});
let renders=0;
const ui=context.window.StockResearchUI.mount({
 getStocks:()=>market.stocks,getMarket:()=>market,redraw:()=>renders++
});
ui.setMarket(market);
assert.equal(market.stocks[0]._quality.state,'unavailable');
(async()=>{
 await ui.refreshQuality();
 assert.equal(market.stocks[0]._quality.state,'matched');
 assert.equal(market.stocks[1]._quality.state,'not-studied');
 assert.equal(renders,1);
 await ui.openQuant(code);
 assert.equal(byId('quantPanel').hidden,false);
 assert.ok(byId('quantResults').innerHTML.includes('66'));
 assert.ok(byId('quantResults').innerHTML.includes('표본 부족'));
 assert.ok(byId('quantContext').textContent.includes(date));
 const initialQuantFetches=requests.filter(x=>x.url.startsWith('quant.json')).length;
 await ui.openQuant('654321');
 assert.ok(byId('quantResults').innerHTML.includes('연구표본에 없음'));
 assert.equal(requests.filter(x=>x.url.startsWith('quant.json')).length,initialQuantFetches);
 byId('closeQuant').events.click();
 assert.equal(byId('quantPanel').hidden,true);
 assert.ok(requests.every(x=>x.opts.cache==='no-store'));
 assert.ok(requests.every(x=>!/(api\.github\.com|workflow_dispatch|token)/.test(x.url)));
 console.log('home_inline_quant_and_reliability_offline_tests_passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
