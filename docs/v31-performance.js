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
if(typeof module!=='undefined'&&module.exports)module.exports={validCoverage,validGroup,outcome};
if(!root.document)return;
const D=root.document,$=id=>D.getElementById(id);
function row(target,values){const tr=D.createElement('tr');values.forEach((v,i)=>{const cell=D.createElement(i===0?'th':'td');if(i===0)cell.scope='row';cell.textContent=v;tr.append(cell);});target.append(tr);}
const pct=n=>number(n)?n.toFixed(2)+'%':'—';
function display(c,r){
 $('schedule').replaceChildren();$('results').replaceChildren();
 if(!validCoverage(c)){$('status').textContent='검증 자료 확인 불가';$('window').textContent='성과 자료 미확보';return;}
 $('status').textContent='최근 점수 기록 '+c.latest_score_asof+' · 공식 진입 시가 대조 '+c.matched_entry_session_count+' / '+c.candidate_entry_session_count+'거래일';
 for(const p of periods){const h=c.horizons[p];row($('schedule'),[p+'개월',h.first_target_date||'대기',h.archive_ready_count+' / '+h.selected_window_count,outcome(c,r,p)?'결과 게시':h.archive_ready_count?'결과 확인 대기':'평가 대기']);}
 const p=$('period').value,s=outcome(c,r,p);
 $('window').textContent=s?'점수 '+s.latest.score_asof+' · 진입 '+s.latest.entry_session+' · 평가 '+s.latest.evaluation_session:c.horizons[p].archive_ready_count?'평가 결과 확인 대기':'평가 대기 · 아직 실측 수익률 없음';
 for(const [k,label] of Object.entries(labels)){const g=s?.groups[k],b=g?.observed_win_rate_bounds_pct;row($('results'),[label,g?[g.valued_count,g.confirmed_opening_count,g.selected_count].join(' / '):'—',pct(g?.observed_mean_return_pct),b?b.map(pct).join(' ~ '):'—',number(g?.observed_mean_gain_loss_ratio)?g.observed_mean_gain_loss_ratio.toFixed(2):'—',g&&g.downside_observed_count?pct(g.worst_entry_relative_mae_pct)+' ('+g.downside_observed_count+'종목)':'—']);}
}
Promise.all(['v3-forward-validation-coverage.json','v3-forward-validation-results.json'].map(async file=>{try{const res=await root.fetch(file,{cache:'no-cache'});return res.ok?await res.json():null;}catch{return null;}})).then(([c,r])=>{$('period').addEventListener('change',()=>display(c,r));display(c,r);});
})(typeof window!=='undefined'?window:globalThis);
