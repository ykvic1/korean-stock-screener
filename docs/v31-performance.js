(function(root){
'use strict';
const periods=['1','2','3','6','12'];
const labels={top3:'상위 3',top10:'상위 10',top20:'상위 20',top30:'상위 30',top100:'상위 100',below40:'40점 미만', '40to60':'40~60점 미만','60to80':'60~80점 미만', '80plus':'80점 이상'};
const count=n=>Number.isSafeInteger(n)&&n>=0;
const date=s=>typeof s==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&Number.isFinite(Date.parse(s+'T00:00:00Z'))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;
const number=n=>typeof n==='number'&&Number.isFinite(n);
function validCoverage(c){
 if(c?.schema!=='v3-forward-validation-coverage-1'||c.performance_validated!==false||!date(c.latest_score_asof)||
 !count(c.candidate_entry_session_count)||!count(c.matched_entry_session_count)||c.matched_entry_session_count>c.candidate_entry_session_count)return false;
 return periods.every(p=>{const h=c.horizons?.[p];return h&&count(h.selected_window_count)&&count(h.archive_ready_count)&&h.archive_ready_count<=h.selected_window_count&&h.selected_window_count<=c.candidate_entry_session_count&&h.archive_ready_count<=c.matched_entry_session_count&&(h.first_target_date===null?h.archive_ready_count===0:date(h.first_target_date));});
}
function validGroup(g){
 if(!g||!['selected_count','confirmed_opening_count','valued_count','unvalued_confirmed_openings_count','positive_count','negative_count','flat_count','downside_observed_count'].every(k=>count(g[k])))return false;
 const s=g.selected_count,o=g.confirmed_opening_count,v=g.valued_count,u=g.unvalued_confirmed_openings_count,w=g.positive_count,l=g.negative_count,d=g.downside_observed_count;
 if(v>o||o>s||v+u!==o||w+l+g.flat_count!==v||d>v)return false;
 const bounds=g.observed_win_rate_bounds_pct;
 if(o===0?bounds!==null:!Array.isArray(bounds)||bounds.length!==2||!bounds.every(number)||bounds[0]<0||bounds[1]>100||bounds[0]>bounds[1]||Math.abs(bounds[0]-100*w/o)>.00011||Math.abs(bounds[1]-100*(w+u)/o)>.00011)return false;
 if(v===0?g.observed_mean_return_pct!==null:!number(g.observed_mean_return_pct)||g.observed_mean_return_pct< -100)return false;
 if(w&&l?!number(g.observed_mean_gain_loss_ratio)||g.observed_mean_gain_loss_ratio<0:g.observed_mean_gain_loss_ratio!==null)return false;
 return d===0?g.worst_entry_relative_mae_pct===null:number(g.worst_entry_relative_mae_pct)&&g.worst_entry_relative_mae_pct>=-100&&g.worst_entry_relative_mae_pct<=0;
}
function outcome(c,r,p){
 if(!validCoverage(c)||!periods.includes(p)||r?.schema!=='v3-forward-validation-results-1'||r.performance_validated!==false)return null;
 const h=c.horizons[p],w=r.horizons?.[p],s=w?.latest;
 if(!s||!count(w.published_window_count)||w.published_window_count<1||w.published_window_count>h.archive_ready_count||!date(s.score_asof)||s.score_asof>c.latest_score_asof||!date(s.entry_session)||s.entry_session<s.score_asof||!date(s.evaluation_session)||s.evaluation_session<s.entry_session||!h.first_target_date||s.evaluation_session<h.first_target_date)return null;
 const g=s.v31_summary;
 if(!g||!Object.keys(labels).every(k=>validGroup(g[k])))return null;
 if([3,10,20,30,100].some(n=>g['top'+n].selected_count>n))return null;
 if(g.top20.confirmed_opening_count!==s.confirmed_opening_count||g.top20.valued_count!==s.valued_count)return null;
 return {latest:s,groups:g};
}
function benchmarkGroup(s,k){
 const b=s?.latest.index_benchmark,g=s?.groups[k],row=b?.groups?.[k];
 if(!g||!row||b.source!=='FinanceDataReader KS11/KQ11 (secondary index reference)'||
 b.official_krx_index_certified!==false||b.entry_session!==s.latest.entry_session||
 b.evaluation_session!==s.latest.evaluation_session||row.valued_count!==g.valued_count||
 !count(row.matched_valued_count)||row.matched_valued_count>row.valued_count)return null;
 const n=row.observed_mean_excess_return_pct_points;
 return row.valued_count>0&&row.matched_valued_count===row.valued_count&&number(n)?n:null;
}
const factorLabels={long_horizon_growth:'장기 거래대금 증가',persistence:'지속성',concentration_quality:'거래 집중도',up_down_asymmetry:'상승·하락 거래 비대칭',post_spike_absorption:'급등 후 소화',correction_contraction:'조정 거래 감소',weekly_structure_increment:'주봉 거래 구조',obv_price_divergence:'OBV·가격 괴리',daily_pattern:'일봉 패턴',weekly_pattern:'주봉 패턴',ma20_ma60_cross_near_ma240:'20·60선 교차',daily_rsi:'일봉 RSI',weekly_rsi:'주봉 RSI',long_ma_position:'장기 이평 위치',independent_breakout_confirmation:'돌파 확인',revenue_growth:'매출 성장',operating_growth_margin:'영업이익 성장·마진',cashflow_fcf_persistence:'현금흐름·FCF',balance_sheet_strength:'재무 건전성',per_eps_quality_growth:'PER·EPS',pbr_equity_quality:'PBR',fcf_yield:'FCF 수익률',sales_to_cap:'매출/시총',existing_momentum:'기존 모멘텀',daily_ma_slope_turn:'일봉 이평 방향',weekly_ma_slope_turn:'주봉 이평 방향'};
function factorRows(s,market){
 const f=s?.latest.factor_validation;
 if(!['KOSPI','KOSDAQ'].includes(market)||f?.status!=='observed_descriptive_only'||
 f.minimum_valued_per_side!==20||f.threshold!=='half_of_full_factor_weight'||!f.factors)return [];
 const rows=[];
 for(const [key,factor] of Object.entries(f.factors)){
  const label=factorLabels[key.split('.')[1]],m=factor?.markets?.[market];
  if(!label||!m||!count(m.partial_weight_count)||!count(m.missing_weight_count))continue;
  const valid=a=>a&&count(a.valued_count)&&count(a.confirmed_opening_count)&&count(a.selected_count)&&a.valued_count<=a.confirmed_opening_count&&a.confirmed_opening_count<=a.selected_count&&(a.valued_count?number(a.observed_mean_return_pct):a.observed_mean_return_pct===null);
  if(!valid(m.high)||!valid(m.low))continue;
  const spread=m.high.valued_count>=20&&m.low.valued_count>=20&&number(m.observed_mean_return_spread_pct_points)?m.observed_mean_return_spread_pct_points:null;
  rows.push({label,high:m.high,low:m.low,spread,partial:m.partial_weight_count,missing:m.missing_weight_count});
 }
 return rows;
}
if(typeof module!=='undefined'&&module.exports)module.exports={validCoverage,validGroup,outcome,benchmarkGroup,factorRows};
if(!root.document)return;
const D=root.document,$=id=>D.getElementById(id);
function row(target,values){const tr=D.createElement('tr');values.forEach((v,i)=>{const cell=D.createElement(i===0?'th':'td');if(i===0)cell.scope='row';cell.textContent=v;tr.append(cell);});target.append(tr);}
const pct=n=>number(n)?n.toFixed(2)+'%':'—';
function display(c,r){
 $('schedule').replaceChildren();$('results').replaceChildren();$('factors').replaceChildren();$('indexStatus').textContent='';$('factorStatus').textContent='성과 자료 대기';
 if(!validCoverage(c)){$('status').textContent='검증 자료 확인 불가';$('window').textContent='성과 자료 미확보';return;}
 $('status').textContent='최근 점수 기록 '+c.latest_score_asof+' · 공식 진입 시가 대조 '+c.matched_entry_session_count+' / '+c.candidate_entry_session_count+'거래일';
 for(const p of periods){const h=c.horizons[p];row($('schedule'),[p+'개월',h.first_target_date||'대기',h.archive_ready_count+' / '+h.selected_window_count,outcome(c,r,p)?'결과 게시':h.archive_ready_count?'결과 확인 대기':'평가 대기']);}
 const p=$('period').value,s=outcome(c,r,p);
 $('window').textContent=s?'점수 '+s.latest.score_asof+' · 진입 '+s.latest.entry_session+' · 평가 '+s.latest.evaluation_session:c.horizons[p].archive_ready_count?'평가 결과 확인 대기':'평가 대기 · 아직 실측 수익률 없음';
 for(const [k,label] of Object.entries(labels)){const g=s?.groups[k],b=g?.observed_win_rate_bounds_pct;row($('results'),[label,g?[g.valued_count,g.confirmed_opening_count,g.selected_count].join(' / '):'—',pct(g?.observed_mean_return_pct),b?b.map(pct).join(' ~ '):'—',number(g?.observed_mean_gain_loss_ratio)?g.observed_mean_gain_loss_ratio.toFixed(2):'—',g&&g.downside_observed_count?pct(g.worst_entry_relative_mae_pct)+' ('+g.downside_observed_count+'종목)':'—',number(benchmarkGroup(s,k))?benchmarkGroup(s,k).toFixed(2):'—']);}
 const b=s?.latest.index_benchmark;
 $('indexStatus').textContent=b?.entry_session===s?.latest.entry_session&&b?.evaluation_session===s?.latest.evaluation_session&&b?'시장지수 참고 수익률: 코스피 '+pct(b.index_return_pct?.KOSPI)+' · 코스닥 '+pct(b.index_return_pct?.KOSDAQ):'시장지수 비교 자료 대기';
 const factors=factorRows(s,$('factorMarket').value);
 $('factorStatus').textContent=factors.length?'실측 표본의 지표별 비교':'실측 지표 비교 자료 대기';
 for(const f of factors)row($('factors'),[f.label,f.high.valued_count+' / '+f.high.selected_count,pct(f.high.observed_mean_return_pct),f.low.valued_count+' / '+f.low.selected_count,pct(f.low.observed_mean_return_pct),number(f.spread)?f.spread.toFixed(2):'—',f.partial+' / '+f.missing]);
}
Promise.all(['v3-forward-validation-coverage.json','v3-forward-validation-results.json'].map(async file=>{try{const res=await root.fetch(file,{cache:'no-cache'});return res.ok?await res.json():null;}catch{return null;}})).then(([c,r])=>{$('period').addEventListener('change',()=>display(c,r));$('factorMarket').addEventListener('change',()=>display(c,r));display(c,r);});
})(typeof window!=='undefined'?window:globalThis);
