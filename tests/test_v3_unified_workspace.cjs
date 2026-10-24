'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const unified=require('../docs/workspace.js');
const legacy=require('../docs/v3-ranking.js');
const html=fs.readFileSync(path.join(__dirname,'../docs/workspace.html'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../docs/workspace.css'),'utf8');
const entry=fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
const backup=fs.readFileSync(path.join(__dirname,'../docs/legacy.html'),'utf8');
const source=fs.readFileSync(path.join(__dirname,'../docs/workspace.js'),'utf8');
const ranking=fs.readFileSync(path.join(__dirname,'../docs/v3-ranking.js'),'utf8');
// Verified PRIVATE KRX derived backfill is a separate research progress meter.
// It NEVER claims adjusted-history price certification or historical PIT.
const krxProgress=require('../docs/v3-krx-progress.js');
const syntheticKrxProgress={
 schema:'v3-verified-private-derived-krx-historical-progress-0.1',
 source_candidate_start:'2016-10-17',source_candidate_end:'2026-10-16',
 five_session_cohorts_target:100,five_session_cohorts_completed:10,
 five_session_cohorts_remaining:90,source_history_coverage_ratio:.1,
 last_completed_source_session:'2017-01-30',
 cross_cohort_bridges_completed:8,
 cross_cohort_bridges_required_for_completed_source:9,
 cross_cohort_bridges_backlog:1,
 rolling_return_horizon_research:{
  '20':{complete_cohort_windows:6,
        possible_windows_from_current_verified_bridges:6,
        backlog_windows_at_this_snapshot:0,
        unadjusted_price_return_approved:false},
  '60':{complete_cohort_windows:0,
        possible_windows_from_current_verified_bridges:0,
        backlog_windows_at_this_snapshot:0,
        unadjusted_price_return_approved:false},
  '120':{complete_cohort_windows:0,
         possible_windows_from_current_verified_bridges:0,
         backlog_windows_at_this_snapshot:0,
         unadjusted_price_return_approved:false},
  '250':{complete_cohort_windows:0,
         possible_windows_from_current_verified_bridges:0,
         backlog_windows_at_this_snapshot:0,
         unadjusted_price_return_approved:false}
 },
 price_discontinuity_heuristic_cohorts_reviewed:9,
 price_discontinuity_review_backlog_at_verified_bridges:0,
 private_immutable_evidence_tails_verified:true,
 private_repo_verified_before_read:true,
 historical_delisted_universe_pit_verified:false,
 corporate_actions_or_adjusted_prices_verified:false,
 official_exchange_historical_factor_certified:false,
 industry_15_or_other_v3_trade_score_promoted:false,
 public_issuer_prices_or_individual_returns_written:false,
 provider_data_new_calls:0
};
const sourceProgress=krxProgress.validate(syntheticKrxProgress);
assert.ok(sourceProgress);
assert.equal(sourceProgress.completed,10);
assert.equal(sourceProgress.windows[20],6);
assert.equal(sourceProgress.risk,9);
assert.equal(krxProgress.validate({
 ...syntheticKrxProgress,five_session_cohorts_completed:11
}),null);
assert.equal(krxProgress.validate({
 ...syntheticKrxProgress,corporate_actions_or_adjusted_prices_verified:true
}),null);
assert.ok(html.includes('id="krxHistoryProgress"'));
assert.ok(entry.includes('id="krxHistoryProgress"'));
assert.ok(entry.includes('v3-krx-progress.js'));


// Industry earnings dashboard is a source-coverage tool, not a scored
// signal. Reject forged source dates, wrong rubrics or sample leakage.
const officialIndustrySample=JSON.parse(fs.readFileSync(
 path.join(__dirname,'../docs/v3-dart-industry-earnings-sample.json'),'utf8'));
const observedIndustry=unified.industryEvidenceView(
 officialIndustrySample,officialIndustrySample.asof);
assert.ok(observedIndustry&&observedIndustry.coverage>=observedIndustry.matched);
assert.ok(observedIndustry.html.includes('표본'));
assert.ok(observedIndustry.html.includes('미확보 반영 범위'));
assert.ok(html.includes('id="industryResearch"'));
assert.ok(entry.includes('id="industryResearch"'),
 'root index must include data panel or JS load would crash on missing element');
assert.ok(source.includes("'v3-dart-industry-earnings-sample.json'"));

assert.ok(source.includes("'v3-industry-observed-relative-strength.json'"));
assert.ok(entry.includes('v3-industry-observed-relative-strength.csv'));
const industryPriceSummary={
 schema:'v3-current-company-industry-relative-strength-observed-v1',
 asof:'2026-10-22',listed_total:100,groups_total:1,
 paired_20_60_observations:85,groups_early_recovery_watch:1,
 relative120_observations:60,
 historical_krx_sector_membership_pit_verified:false,
 official_industry_15_points_authorized:false,
 investment_grade_100point_scores_authorized:false,
 independently_verified_benchmark_session_or_adjusted_prices:false,
 new_provider_calls:0,
 source_sha256:{market:'a'.repeat(64),catalog:'b'.repeat(64),
                current_dart_company_industry_codes:'c'.repeat(64)},
 groups:[{
  market:'KOSPI',current_dart_industry_prefix_2:'20',
  current_listed_issuers:100,same_day_priced_issuers:85,
  complete_relative20_60_issuer_count:85,
  missing_relative20_60_issuer_count:15,observed_20_60_coverage:.85,
  relative120_issuer_count:60,missing_relative120_issuer_count:40,
  observed_120_coverage:.6,median_market_relative_120pct_sample:3.25,
  median_market_relative_20pct_sample:2,
  median_market_relative_60pct_sample:-1,
  positive_relative20_issuers_sample:55,
  positive_relative20_breadth_observed_only:.647059,
  positive_relative20_breadth_all_lower_bound:.55,
  positive_relative20_breadth_all_upper_bound:.7,
  early_rotation_research_state:'early_relative_recovery_watch',
  current_company_codes_not_historical_krx_sector:true,
  market_benchmark_dates_independently_verified:false,
  prices_corporate_action_adjusted_verified:false,
  official_industry_15_points:null
 }]
};
const currentSector=unified.industryPriceEvidenceView(
 industryPriceSummary,'2026-10-22',100);
assert.ok(currentSector&&currentSector.watch===1);
assert.ok(currentSector.html.includes('관찰 후보 1개'));
assert.ok(currentSector.html.includes('120일 상대강도'));
assert.ok(currentSector.html.includes('+3.3%p'));
assert.equal(currentSector.observed120,60);
assert.equal(currentSector.byKey.get('KOSPI:20').m120,3.25);
const bad120=structuredClone(industryPriceSummary);
bad120.groups[0].relative120_issuer_count=61;
assert.equal(unified.industryPriceEvidenceView(bad120,'2026-10-22',100),null);
const no120=structuredClone(industryPriceSummary);
delete no120.relative120_observations;
for(const key of ['relative120_issuer_count','missing_relative120_issuer_count',
 'observed_120_coverage','median_market_relative_120pct_sample'])
 delete no120.groups[0][key];
assert.equal(unified.industryPriceEvidenceView(no120,'2026-10-22',100)
 .byKey.get('KOSPI:20').m120,null);
const classifiedRecords={};
for(let i=1;i<=100;i++)
 classifiedRecords[String(i).padStart(6,'0')]={
  market:'KOSPI',status:'observed',industry_code:'20123',
  checked_on:'2026-10-21',origin:'OpenDART_company_json',
  historical_pit_verified:false,corporation_identity_digest:'d'.repeat(64)
 };
const classified={
 schema:'v3-dart-current-listed-industry-code-reference-0.1',
 asof:'2026-10-22',listed_total:100,observed_industry_codes:100,
 missing_industry_codes:0,not_yet_attempted:0,
 official_krx_sector_membership_verified:false,
 past_constituent_membership_pit_verified:false,
 industry_5433_points_authorized:false,investable_v3_score_certified:false,
 source_records_digest:'f'.repeat(64),records:classifiedRecords
};
const mockModel={date:'2026-10-22',marketEligible:100,rows:[
 {r:{code:'000001',name:'example',market:'KOSPI'}}
]};
const sectorAttached=unified.applyIndustryReference(mockModel,classified,currentSector);
assert.equal(sectorAttached.mapped,1);
assert.equal(mockModel.rows[0].r.v3_sector_research.name,'화학제품(의약품 제외)');
assert.equal(mockModel.rows[0].r.v3_sector_research.group.m120,3.25);
assert.ok(unified.sectorEvidenceMarkup(mockModel.rows[0].r,true).includes('+3.3%p'));
assert.ok(unified.sectorEvidenceMarkup(mockModel.rows[0].r,true).includes('점수 배점 제외'));
const badClassified=structuredClone(classified);
badClassified.records['000001'].market='KOSDAQ';
assert.equal(unified.applyIndustryReference(mockModel,badClassified,currentSector),null);
assert.equal(mockModel.rows[0].r.v3_sector_research.name,'화학제품(의약품 제외)');
assert.equal(unified.industryPriceEvidenceView(
 industryPriceSummary,'2026-10-21',100),null);
const forgedIndustryPrice=structuredClone(industryPriceSummary);
forgedIndustryPrice.groups[0].official_industry_15_points=3;
assert.equal(unified.industryPriceEvidenceView(
 forgedIndustryPrice,'2026-10-22',100),null);
const contaminatedIndustryPrice=structuredClone(industryPriceSummary);
contaminatedIndustryPrice.groups[0].current_dart_industry_prefix_2='<img>';
assert.equal(unified.industryPriceEvidenceView(
 contaminatedIndustryPrice,'2026-10-22',100),null);

const dayBeforeIndustry=new Date(officialIndustrySample.asof+'T00:00:00Z');
dayBeforeIndustry.setUTCDate(dayBeforeIndustry.getUTCDate()-1);
assert.equal(unified.industryEvidenceView(
 officialIndustrySample,dayBeforeIndustry.toISOString().slice(0,10)),null,
 'industry evidence must reject different source dates, not the actual live asof');
assert.equal(unified.industryEvidenceView(
 {...officialIndustrySample,sector_15_points_earned_issuer_count:1},
 officialIndustrySample.asof),null);
const mutated=structuredClone(officialIndustrySample);
mutated.groups[0].group_15point_investment_score=3;
assert.equal(unified.industryEvidenceView(mutated,mutated.asof),null);
const spoof=structuredClone(officialIndustrySample);
spoof.groups[0].dart_industry_code_prefix_2='<img onerror=alert(1)>';
assert.equal(unified.industryEvidenceView(spoof,spoof.asof),null);
// A fully audited real group may legitimately have an observed zero.
// Never render it as "no data"; only missing remains a dash.
const sample=structuredClone(officialIndustrySample);
const sampleRow=sample.groups.find(g=>g.issuer_year_pair_observed>=3);
assert.ok(sampleRow);
sampleRow.observed_earnings_improving_issuer_ratio_sample_only=0;
sampleRow.improvement_breadth_lower_bound_known_classified_only=0;
sampleRow.improvement_breadth_upper_bound_known_classified_only=0;
sampleRow.classified_sample_earnings_coverage_ratio=
  sampleRow.issuer_year_pair_observed/sampleRow.current_classified_issuers;
assert.ok(unified.industryEvidenceView(sample,sample.asof)
 .html.includes('0.0%'));
// A weekly group is evidence replay, NOT full verification or a fresh API response.
assert.equal(unified.weeklyCoverageText(null,'2026-10-20'),'');
const weekly={schema:'v3-seven-day-research-coverage-0.1',
 market_asof:'2026-10-19',run_kst:'2026-10-20',cycle_start:'2026-10-20',
 listed_total:2497,processed_this_cycle:357,unprocessed_this_cycle:2140,
 cohort_counts:[357,357,357,357,357,356,356],
 source_collection_calls:0,verified_full_six_pillar_issuer_count:0,
 source_reference_counts_this_cycle:{technical_parts:321}};
assert.ok(unified.weeklyCoverageText(weekly,'2026-10-20')
 .includes('357/2497종목'));
assert.ok(unified.weeklyCoverageText(weekly,'2026-10-20')
 .includes('기존 원본 재계산'));
assert.equal(unified.weeklyCoverageText({...weekly,source_collection_calls:6},
 '2026-10-20'),'');
assert.equal(unified.weeklyCoverageText({...weekly,processed_this_cycle:400},
 '2026-10-20'),'');
// Genuine FCF source receipts show counts only, never a synthetic 15pt rank.
const rawFCF={schema:'v3-actual-dart-fcf-raw-feature-and-peer-preview-0.1',
 asof:'2026-10-20',listed_total:2497,
 source_same_filing_scope_receipt_checked:true,
 hist_PIT_verified:false,four_year_financial15_complete:false,
 valuation15_complete:false,joined_to_main_v3_ranking:false,
 buy_recommendations_authorized:false,six_pillar_confirmed_100pt:null,
 minimum_same_market_peers:30,financial_fcf_weight_available_max:4,
 valuation_fcf_weight_available_max:5,
 real_fcf_assets_three_year_raw_issuer_count:24,
 real_latest_fcf_yield_raw_issuer_count:33,
 financial_fcf4_research_candidate_issuer_count:0,
 valuation_fcf5_research_candidate_issuer_count:0,rows:[]};
assert.ok(unified.actualCashflowCoverageText(rawFCF,'2026-10-20',2497)
 .includes('실제 FCF 3년 자료 24종목'));
assert.ok(unified.actualCashflowCoverageText(rawFCF,'2026-10-20',2497)
 .includes('4점 잠정계산 0종목'));
assert.equal(unified.actualCashflowCoverageText(rawFCF,'2026-10-21',2497),'');
assert.equal(unified.actualCashflowCoverageText(rawFCF,'2026-10-20',2496),'');
assert.equal(unified.actualCashflowCoverageText(
 {...rawFCF,buy_recommendations_authorized:true},'2026-10-20',2497),'');
assert.equal(unified.actualCashflowCoverageText(
 {...rawFCF,financial_fcf4_research_candidate_issuer_count:25},
 '2026-10-20',2497),'');
assert.equal(unified.actualCashflowCoverageText(
 {...rawFCF,hist_PIT_verified:true},'2026-10-20',2497),'');
assert.ok(source.includes("'v3-dart-fcf-research-preview.json'")&&
 source.includes('actualCashflowCoverageText(actualCashflowCoverage,model.date,model.marketEligible)'));
// Four-year DART source is read by primary V3; reference CAGR/margin
// remains informational until revision/PIT and certified 20+20 scoring.
const fourRecords=[2022,2023,2024,2025].map(yr=>({
 year:yr,fs_div:'CFS',currency:'KRW',
 receipt_id:String(yr+1)+'0318001162',values:{revenue:1000+yr}
}));
const annualFour={schema:'v3-four-year-dart-financial-evidence-0.1',
 asof:'2026-10-19',listed_total:2497,pilot_issuer_count:1,
 four_years_all_financial_pillars_certified:false,full_v3_ready:false,
 raw_account_rows_stored:false,confirmed_six_pillar_ranks:null,
 records:[{code:'005930',v3_full100_investable_score:null,
 financial15_confirmed_points:null,valuation15_confirmed_points:null,
 four_year_financial_pit_authenticated:false,
 historical_revisions_authenticated:false,buy_certified:false,
 same_scope_four_years:true,accounting_scope:'CFS',
 reports:fourRecords,annual_revenue_cagr3_reference_pct:12.3,
 three_year_operating_margin_mean_reference_pct:9.2,
 fcf_assets_reference_pct:null,net_debt_equity_reference_pct:null,
 valuation_official_pbr:null}]};
const dated=legacy.observedFourYearEvidence(annualFour,'2026-10-20',2497);
assert.equal(dated.get('005930').cagr,12.3);
assert.equal(dated.get('005930').margin,9.2);
assert.equal(dated.get('005930').authenticated,false);
assert.equal(legacy.observedFourYearEvidence(
 {...annualFour,asof:'2026-10-21'},'2026-10-20',2497).size,0,
 'future source date cannot be used');
assert.equal(legacy.observedFourYearEvidence(
 annualFour,'2026-11-25',2497).size,0,'stale fiscal source must expire');
assert.ok(source.includes('v3-dart-four-year-reference.json')&&
 source.includes('core.prepare(...responses.slice(0,6),responses[8])'),
 'primary workspace must ingest separate four-year DART evidence');
assert.ok(source.includes('4개년 연간공시 연구 참고')&&
 source.includes('공시 정정·시점(PIT) 검증 전으로 재무 20점과 가치 20점은 정식 투자점수 미확정'),
 'four-year metrics cannot silently become a certified 30 point ranking');
assert.equal(Object.values(unified.WEIGHTS).reduce((a,b)=>a+b,0),100);
assert.deepEqual(unified.WEIGHTS,
 {volume:20,technical:25,financial:20,valuation:20,momentum:15});
assert.equal(unified.PILLARS[0][0],'volume');
assert.equal(unified.PILLARS[0][1],'거래량·OBV');
assert.deepEqual(unified.VOLUME_COMPONENTS.map(([label,w])=>w),[6,6,4,4]);
assert.ok(source.includes("v3-volume-obv-observed.json"));
assert.ok(source.includes("applyObservedVolume(model,responses[13])"));
assert.ok(source.includes('data-pillar="\'+esc(i.key)'));
assert.ok(source.includes('거래량·OBV /20'));
assert.ok(css.includes('.u-scoresix span[data-pillar="volume"] small'));
assert.equal(unified.validDate('2026-02-30'),false,'calendar rollover is invalid');
assert.deepEqual(unified.marketFreshness('2026-10-20',new Date('2026-10-19T15:10:00Z')),
 {status:'current',calendarDays:0},'KST midnight boundary');
assert.deepEqual(unified.marketFreshness('2026-10-19',new Date('2026-10-19T15:10:00Z')),
 {status:'current',calendarDays:1});
assert.deepEqual(unified.marketFreshness('2026-10-19',new Date('2026-10-26T00:00:00Z')),
 {status:'stale',calendarDays:7});
assert.deepEqual(unified.marketFreshness('2026-10-21',new Date('2026-10-20T00:00:00Z')),
 {status:'future',calendarDays:-1});
assert.ok(html.includes('id="unifiedApp"'));
assert.ok(html.includes('<details class="u-intro" id="marketSummary">'),
 'market observation details must be collapsed on initial mobile load');
assert.ok(!html.includes('<details class="u-intro" id="marketSummary" open'),
 'default market observation must be closed');
assert.ok(html.includes('<details class="u-extra-filters" id="advancedFilters">'),
 'oversized search filter area must be optional, not fixed');
assert.ok(html.indexOf('id="stockSearch"')<html.indexOf('id="tabs"'),
 'global ticker search must be above secondary chart and RSI tabs');
assert.ok(source.includes("if(search)return"),'search must bypass TOP100 and tab scopes');
assert.ok(source.includes("미검증"),'avoid showing dash-only factors without explanation');
assert.ok(source.includes("+' /'+i.max"),'each domain must show its approved maximum');
assert.ok(html.includes('id="top100Only"'),'one-tap top 100 filter required');
assert.ok(html.includes('<option value="score" selected>V3 잠정점수순</option>'),
 'default selection must explicitly use V3 provisional observations');
assert.ok(source.includes("sort='score'"),'default rank must use displayed score');
assert.ok(css.includes('color-scheme:dark')&&css.includes('.u-scoresix'),
 'approved dark-green theme and 6 V3 pillar boxes must remain');
assert.ok(css.includes('.u-scoresix span.u-total-factor')&&
 css.includes('box-shadow:inset 0 0 0 1.5px #e66a72'),
 'each unverified factor card should have a red inner outline');
assert.ok(source.includes('x.composite.pillars.map')&&
 source.includes("i.candidate===null?'—':i.candidate.toFixed(1)"),
 'calculated provisional research candidates remain visible');
assert.ok(source.includes('researchComposite(r)')&&
 source.includes('산출 가능 배점'),
 'V3 score coverage must accompany any available research sum');
assert.ok(source.includes('잠정 합계')&&source.includes('미검증'),
 'do not treat research aggregate as a verified investment decision');
assert.equal((html.match(/id="stockSearch"/g)||[]).length,1);
for(const s of ['id="stockList"','id="detail"','id="market"','id="sort"',
 'id="tabs"','id="saferOnly"','id="reload"','id="statV3"',
 '시장 · 정렬 · 손절 위험 상세 설정','V3 잠정점수 산출 종목','차트 패턴'])
 assert.ok(html.includes(s),'single screen missing '+s);
assert.ok(html.includes('workspace.js')&&html.includes('v3-ranking.js'));
assert.ok(entry.includes('id="unifiedApp"')&&entry.includes('workspace.js'),
 'homepage itself must serve unified V3 without a redirect');
assert.ok(!entry.includes('http-equiv="refresh"'),'avoid legacy intermediate page or double navigation');
assert.ok(backup.includes('통합 스크리너로 돌아가기'),'archived previous UI must be available');
assert.ok(!backup.includes('http-equiv="refresh"'),'old research UI should not auto-redirect');
assert.ok(ranking.includes("if(root.document.getElementById('rankStatus'))init();"),
 'legacy beta mounting cannot crash the new unified main app');
assert.ok(css.includes('@media(max-width:860px)')&&css.includes('@media(max-width:480px)'),
 'mobile stacked research UI is required');
assert.ok(html.includes("script-src 'self'")&&html.includes("connect-src 'self'"),
 'CSP must disallow untrusted JS/data imports');
assert.ok(source.includes('six_pillar_full_candidate_only')===false,
 'never build a full v3 investment ranking in a front-end shortcut');
assert.ok(!source.includes('localStorage')&&!source.includes('github_token'),
 'UI must not require credentials or persistent private trade settings');

const shape={
 schema:'v3-pattern-first-daily-research-0.1',status:'heuristic_unverified',
 timeframe:'daily',asof:'2026-10-20',completed_week_only:undefined,
 rsi14:44,rsi30_cross_recent:true,rsi_bullish_divergence_recent:true,
 patterns:[{type:'bull_flag',stage:'forming'}],
 pattern_geometry:{status:'heuristic_unvalidated',
  falling_wedge_candidate:false,inverse_head_shoulders_breakout_candidate:false,
  triangle_candidate:false,post_rise_consolidation_candidate:false},
 scoring_allowed:false,buy_signal:false,official_regular_session_verified:false
};
const row={
 code:'005930',name:'샘플회사',market:'KOSPI',
 price:100,stop_line:91,stop_pct:-9,score:70,coverage_pct:70,
 price20:1,price60:-7,risk_flags:[],price_date:'2026-10-20',
 patterns:[],obv_rising:false,divergence:false,phase:'조정 중',
 pattern_research:shape,
 pattern_weekly_research:{...shape,timeframe:'weekly',asof:'2026-10-16',
  completed_week_only:true,rsi30_cross_recent:false,rsi_bullish_divergence_recent:false,
  patterns:[{type:'double_bottom',stage:'breakout_observed'}]},
 v3_pattern_integrated:{schema:'v3-pattern-within-six-pillars-research-0.4',
  asof:'2026-10-20',technical_maximum:25,
  technical_research_candidate:22.8,investable_score:null,
  buy_certified:false,v3_release_ready:false}
};
assert.ok(unified.validPattern(row,'daily'));
assert.ok(unified.validPattern(row,'weekly'));
assert.ok(unified.evidence(row,'daily').some(x=>x.label==='Bull Flag'));
assert.ok(unified.evidence(row,'weekly').some(x=>x.label==='더블바텀'));
assert.ok(unified.evidence(row,'daily').some(x=>x.label==='RSI 30 재돌파'));
assert.ok(unified.evidence(row,'daily').some(x=>x.label==='RSI 상승다이버전스'));
assert.equal(unified.technicalCandidate(row),22.8);
assert.deepEqual(unified.pillarState(row).map(x=>x.candidate),
 [null,22.8,null,null,null]);
// Current five-area total is 100. Industry has no hidden 15-point allocation.
assert.equal(Object.values(unified.WEIGHTS).reduce((a,b)=>a+b,0),100);
assert.equal(unified.INDUSTRY_COMPONENTS,undefined);
assert.equal(unified.industryParts,undefined);
assert.equal(unified.researchComposite(row).pillars.length,5);
assert.ok(source.includes('소속 업종 · 시장 대비 20·60·120일 상대강도'));
assert.ok(!Object.prototype.hasOwnProperty.call(unified.WEIGHTS,'industry'),
 'legacy 15-point industry research is not a sixth scored factor');
assert.equal(typeof unified.fiveAreaResearchView,'undefined');
const missingVolume=unified.pillarState(row)[0];
assert.equal(missingVolume.label,'거래량·OBV');
assert.equal(missingVolume.max,20);
assert.equal(missingVolume.candidate,null);
assert.equal(missingVolume.available,0);
assert.ok(unified.volumeStatus(missingVolume).includes('결측은 0점이 아닙니다'));
const volumeRow={asof:'2026-10-20',market:'KOSPI',observed_points:13,
 observed_available_max:16,unobserved_max:4,
 subpoints:{sustained_volume:3,obv_60_direction:6,
            obv_price_not_reflected:4,post_spike_absorption:null},
 risk_flags:[],source_history_adjusted_verified:false,
 source_exchange_regular_only_verified:false,
 foreign_institutional_net_buy_inferred:false,
 investable_score_certified:false,trading_signal_authorized:false};
const withVolume={...row,v3_volume_observed:volumeRow};
assert.deepEqual(unified.observedVolumeEvidence(withVolume),
 {earned:13,available:16,fullyObserved:false});
assert.equal(unified.researchComposite(withVolume).earned,35.8);
assert.equal(unified.researchComposite(withVolume).available,41);
assert.ok(unified.volumeStatus(unified.pillarState(withVolume)[0])
 .includes('매집 증거 또는 매수 신호가 아닙니다'));
assert.ok(unified.volumeParts(withVolume).includes('매물 소화'));
assert.ok(unified.volumeParts(withVolume).includes('미확보'));
for(const malformed of [
 {...volumeRow,asof:'2026-10-21'},
 {...volumeRow,observed_points:99},
 {...volumeRow,foreign_institutional_net_buy_inferred:true},
 {...volumeRow,subpoints:{...volumeRow.subpoints,post_spike_absorption:4}},
 {...volumeRow,investable_score_certified:true}
])assert.equal(unified.observedVolumeEvidence({...row,v3_volume_observed:malformed}),null);
assert.equal(unified.pillarState({...row,score:100})[0].candidate,null,
 'old v2.2 score cannot be inserted into observed V3 volume points');
assert.equal(unified.pillarState({...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,
 six_pillar_maximum:{volume:20,technical:25,financial:15,
                     valuation:15,industry:15,momentum:10},
 six_pillar_candidates:{volume:20,ias:20}
}})[0].candidate,null,
 'synthetic old IAS candidate cannot masquerade as actual observed volume');
assert.ok(!unified.riskState({r:row},legacy).unsafe);
assert.equal(unified.riskState({r:{...row,stop_line:60,stop_pct:-40}},legacy).unsafe,true);
assert.equal(unified.technicalCandidate({...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,investable_score:70}}),null,
 'forged completed trade score invalidates display');
assert.equal(unified.technicalCandidate({...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,technical_research_candidate:26}}),null);
for(const item of [
 {...row,pattern_research:{...shape,asof:'2026-10-21'}},
 {...row,pattern_research:{...shape,buy_signal:true}},
 {...row,pattern_research:{...shape,patterns:[{type:'invented',stage:'forming'}]}},
 {...row,pattern_research:{...shape,scoring_allowed:true}},
])assert.equal(unified.validPattern(item,'daily'),null,'invalid daily source must fail closed');
for(const item of [
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,completed_week_only:false}},
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,asof:'2026-10-23'}},
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,asof:'2026-10-17'}},
 {...row,pattern_weekly_research:{...row.pattern_weekly_research,asof:'2026-10-02'}}
])assert.equal(unified.validPattern(item,'weekly'),null,'incomplete/stale/future week must fail closed');
assert.equal(unified.esc('<script>"/&'), '&lt;script&gt;&quot;/&amp;');

// Only calculable same-date research candidates can be shown, even if
// the source has not yet certified them for trading. Never insert missing=0.
const partial={...row,v3_pattern_integrated:{
 ...row.v3_pattern_integrated,
 technical_research_candidate:null,
 technical_observed_maximum:11,
 technical_parts:{
  daily_pattern:3.5,weekly_pattern:null,daily_rsi:1,weekly_rsi:null,
  weekly_ma120:null,volume_confirmation:2
 }
}};
assert.equal(unified.technicalCandidate(partial),null,
 'incomplete technical factor cannot masquerade as fully scored 25-point factor');
assert.deepEqual(unified.technicalEvidence(partial),
 {earned:6.5,available:11,fullyObserved:false});
assert.equal(unified.researchComposite(partial).earned,6.5);
assert.equal(unified.researchComposite(partial).available,11);
assert.equal(unified.researchComposite(partial).missing,89);
assert.deepEqual(unified.pillarState(partial).map(p=>p.candidate),
 [null,6.5,null,null,null]);
// Backfilled V3 values from the bounded 7-day rotation require exact date,
// same ticker, source counts, numeric parts and explicit noncertification.
const missingAdapter={...partial,v3_pattern_integrated:null};
const backedRec={
 code:missingAdapter.code,market_source_asof:'2026-10-20',
 last_recalculated_kst:'2026-10-20',available_weight:11,
 research_score_points:6.5,technical_rebuilt_from_saved_source:true,
 technical_research_backfill:partial.v3_pattern_integrated,
 six_pillar_research_score_confirmed:false
};
const backState={schema:'v3-seven-day-research-source-rotation-0.1',
 market_asof:'2026-10-20',cycle_start:'2026-10-20',run_kst:'2026-10-20',
 source_collection_calls:0,legacy_v22_score_used:false,
 v3_live_full_six_pillar_score_claimed:false,
 records:{'005930':backedRec}};
const backModel={date:'2026-10-20',rows:[{r:missingAdapter}]};
assert.equal(unified.applyRotationTechnicalBackfill(backModel,backState),1);
assert.equal(unified.researchComposite(missingAdapter).earned,6.5);
assert.equal(unified.researchComposite(missingAdapter).available,11);
assert.equal(unified.applyRotationTechnicalBackfill(backModel,backState),0,
 'proven adapter cannot be overwritten on subsequent replay');
for(const bad of [
 {...backState,market_asof:'2026-10-19'},
 {...backState,source_collection_calls:4},
 {...backState,records:{'005930':{...backedRec,research_score_points:100}}},
 {...backState,records:{'005930':{...backedRec,technical_research_backfill:{
  ...partial.v3_pattern_integrated,investable_score:99}}}},
]){
 const shadow={date:'2026-10-20',rows:[{r:{...missingAdapter,v3_pattern_integrated:null}}]};
 assert.equal(unified.applyRotationTechnicalBackfill(shadow,bad),0,
  'source/date/score mismatches must never turn into V3 user scores');
}
const momentumReference={
 schema:'v3-observed-market-momentum-8of10-research-0.1',
 asof:'2026-10-20',market:'KOSPI',momentum_maximum:15,
 momentum_parts:{relative20:3,relative60:2,positive_days20:null,
  trend_persistence60:null,reversal_breadth120:null},
 momentum_observed_maximum:8,momentum_research_candidate:5,
 peer20_count:100,peer60_count:100,
 buy_certified:false,investable_score:null,
 market_benchmark_date_independently_verified:false,
 source_unadjusted_corporate_events_verified:false,
 positive_days20_observed:false
};
const partialMomentum={...row,v3_pattern_integrated:null};
const stateWithMomentum={...backState,records:{
 '005930':{...backedRec,technical_rebuilt_from_saved_source:false,
 technical_research_backfill:null,momentum_research_reference:momentumReference,
 momentum_research_points:5,momentum_available_weight:8}
}};
assert.equal(unified.applyRotationTechnicalBackfill(
 {date:'2026-10-20',rows:[{r:partialMomentum}]},stateWithMomentum),1);
assert.equal(unified.researchComposite(partialMomentum).earned,5);
assert.equal(unified.researchComposite(partialMomentum).available,8);
assert.equal(unified.pillarState(partialMomentum)[4].max,15);
assert.equal(unified.momentumEvidence({...partialMomentum,
 v3_momentum_research:{...momentumReference,momentum_maximum:10}}),null,
 'old ten-point maximum must not be treated as the new fifteen-point area');
assert.equal(unified.momentumEvidence({...partialMomentum,
 v3_momentum_research:{...momentumReference,
 market_benchmark_date_independently_verified:true}}),null);
assert.equal(unified.momentumEvidence({...partialMomentum,
 v3_momentum_research:{...momentumReference,peer20_count:2}}),null);
assert.equal(unified.momentumEvidence({...partialMomentum,
 v3_momentum_research:{...momentumReference,
 momentum_parts:{...momentumReference.momentum_parts,positive_days20:2}}}),null);
// A second all-listed, same-day derived-price replay can cover every
// ALREADY PRICED current issuer without recollecting KRX quotes or
// waiting seven calendar days for a weekly 357-name cohort.
const replayRecord={
 code:row.code,market:row.market,asof:row.price_date,
 technical_research_reference:partial.v3_pattern_integrated,
 momentum_research_reference:momentumReference,
 official_investment_score_certified:false,
 price_source_corporate_action_verified:false
};
const replaySource={
 schema:'v3-all-listed-current-market-price-only-technical-momentum-replay-0.1',
 asof:row.price_date,listed_total:2,priced_current_session:1,
 technical_research_issuer_count:1,momentum_research_issuer_count:1,
 at_least_one_price_factor_issuer_count:1,
 source_market_sha256:'b'.repeat(64),source_catalog_sha256:'a'.repeat(64),
 separate_source_provider_calls:0,official_ias_or_buyer_identity_inferred:false,
 corporate_action_adjusted_verified:false,historical_PIT_certified:false,
 sector_score_authentic:false,certified_100point_investment_issuers:0,
 rows:{[row.code]:replayRecord}
};
const seeded={...row,v3_pattern_integrated:null,v3_momentum_research:null};
const seededModel={date:row.price_date,marketEligible:2,rows:[{r:seeded}]};
assert.equal(unified.applyCurrentPriceReplay(seededModel,replaySource),2);
assert.equal(unified.researchComposite(seeded).available,19);
assert.ok(unified.researchComposite(seeded).earned!==null);
assert.equal(unified.pillarState(seeded)[1].available,11);
assert.equal(unified.pillarState(seeded)[4].available,8);
assert.equal(unified.pillarState(seeded)[4].max,15);
for(const forged of [
 {...replaySource,asof:'2026-10-21'},
 {...replaySource,separate_source_provider_calls:1},
 {...replaySource,technical_research_issuer_count:999},
 {...replaySource,official_ias_or_buyer_identity_inferred:true},
 {...replaySource,rows:{[row.code]:{...replayRecord,
   official_investment_score_certified:true}}},
 {...replaySource,rows:{[row.code]:{...replayRecord,
   momentum_research_reference:{...momentumReference,peer20_count:1}}}}
]){
 const other={...row,v3_pattern_integrated:null,v3_momentum_research:null};
 assert.equal(unified.applyCurrentPriceReplay({
   date:row.price_date,marketEligible:2,rows:[{r:other}]},forged),0);
 assert.equal(unified.researchComposite(other).earned,null);
}
assert.ok(source.includes("applyCurrentPriceReplay(model,responses[14])"));
assert.ok(source.includes("'v3-current-price-technical-momentum.json'"));
const missing={...row,v3_pattern_integrated:null};
assert.equal(unified.researchComposite(missing).earned,null,
 'missing factor must not appear as 0.0/100');
assert.equal(unified.researchComposite(missing).available,0);
const zero={...partial,v3_pattern_integrated:{
 ...partial.v3_pattern_integrated,
 technical_parts:{daily_pattern:0,weekly_pattern:null,daily_rsi:0,
  weekly_rsi:null,weekly_ma120:null,volume_confirmation:0}
}};
assert.equal(unified.researchComposite(zero).earned,0,
 'legitimate calculated zero stays a numeric zero');
assert.equal(unified.researchComposite(zero).available,11);
const timeMismatch={...partial,v3_pattern_integrated:{
 ...partial.v3_pattern_integrated,asof:'2026-10-19'}};
assert.equal(unified.researchComposite(timeMismatch).earned,null,
 'mismatched source date blocks numeric aggregate');
const partial3={...partial,v3_pattern_integrated:{
 ...partial.v3_pattern_integrated,
 six_pillar_maximum:unified.WEIGHTS,
 six_pillar_candidates:{volume:null,technical:null,financial:6,
   valuation:null,industry:null,momentum:4}
}};
assert.equal(unified.researchComposite(partial3).earned,6.5);
assert.equal(unified.researchComposite(partial3).available,11,
 'previously supplied momentum data cannot enter a V3 subtotal without independently checked 8-point benchmark research');
assert.equal(unified.researchComposite({...partial3,
 v3_pattern_integrated:{...partial3.v3_pattern_integrated,
 six_pillar_maximum:{...unified.WEIGHTS,financial:25}}
}).earned,6.5,'out-of-contract candidate maxima cannot inflate a total');

const highLegacy={...row,code:'000001',name:'높은 구형점수',
 score:98,v3_pattern_integrated:null,
 pattern_research:null,pattern_weekly_research:null};
const safe={r:row,q:null,watch:null,quant:false,researchCandidate:false};
const unsafe={r:highLegacy,q:null,watch:null,quant:false,researchCandidate:false};
const model={date:'2026-10-20',rows:[unsafe,safe],marketEligible:2,
 rollingBookCount:0,quantCount:0};
assert.deepEqual(unified.rankItems(model,legacy,'all').map(x=>x.r.code),
 ['005930','000001'],'V2.2 98 points cannot outrank V3 research evidence');
assert.equal(unified.rankItems(model,legacy,'all')[0].composite.earned,22.8);
assert.equal(unified.rankItems(model,legacy,'all')[1].composite.earned,null);
assert.equal(unified.rankItems(model,legacy,'patterns').length,1);
assert.equal(unified.rankItems(model,legacy,'rsi').length,1);
assert.equal(unified.rankItems(model,legacy,'quant').length,0);
assert.deepEqual(unified.rankItems(model,legacy,'all','all','높은').map(x=>x.r.code),
 ['000001']);
assert.equal(unified.summary(model,legacy).officialFullScore,0,
 'provisional sum is not a final investment ranking');
assert.equal(unified.summary(model,legacy).provisional,1);
const safeHighRisk={...row,code:'111111',name:'손절위험',stop_line:60,
 stop_pct:-40,v3_pattern_integrated:{...row.v3_pattern_integrated,
 technical_research_candidate:24}};
const risks={...model,rows:[safe,{...safe,r:safeHighRisk}]};
assert.equal(unified.rankItems(risks,legacy,'all')[0].r.code,'111111',
 'risk warnings should not silently mutate the visible observed-score ordering');
assert.equal(unified.rankItems(risks,legacy,'all','all','','score',true).length,1);
assert.ok(unified.rankItems(risks,legacy,'all')[0].risk.unsafe);

// A 100-candidate browse filter operates ONLY on stocks with research values.
const candidates=Array.from({length:106},(_,i)=>({
 ...safe,r:{...row,code:String(i).padStart(6,'0'),name:'모의'+i,
  score:i, // old 2.2 points deliberately run in opposite direction
  v3_pattern_integrated:{...row.v3_pattern_integrated,
   technical_research_candidate:Math.round((25-i*0.1)*10)/10}}
}));
const many={...model,rows:candidates.reverse()};
const top=unified.rankItems(many,legacy,'all','all','','score',false,true);
assert.equal(top.length,100);
assert.equal(top[0].r.code,'000000');
assert.equal(top[99].r.code,'000099');
assert.equal(top[0].candidatePosition,1);
assert.equal(top[99].candidatePosition,100);
const outsideTop=many.rows[0].r;
const globalSearch=unified.rankItems(many,legacy,'rsi','KOSDAQ',
 outsideTop.code,'score',true,true);
assert.deepEqual(globalSearch.map(x=>x.r.code),[outsideTop.code],
 'search always bypasses the optional top100 and tab filter');
assert.equal(globalSearch[0].candidatePosition,106);
const noEvidence={...model,rows:[unsafe]};
assert.equal(unified.rankItems(noEvidence,legacy,'all','all','','score',false,true).length,0,
 'TOP100 does not fabricate observed stock candidates');
assert.equal(unified.rankItems(noEvidence,legacy,'all').length,1,
 'the full list retains unavailable-source tickers for direct inspection');

// Historical OpenDART BS/IS partial 8+5 points integrate into six-area UI
// only when same-market date, separately verified research provenance matches.
const funded={...row,market:'KOSPI'};
const display={date:'2026-10-20',rows:[{r:funded}]};
const bulkParts={
 schema:'v3-dart-multi-year-research-parts-0.1',
 asof:'2026-10-20',listed_total:2497,model_financial_full_points:15,
 model_valuation_full_points:15,financial_partial_max_possible:8,
 valuation_partial_max_possible:10,
 historical_PIT_certified:false,fully_verified_six_pillar_count:0,
 all_scores_provisional_unverified:true,
 rows:[{code:funded.code,market:'KOSPI',asof:'2026-10-20',
   confirmed_v3_score:null,scoring_certified:false,
   financial_candidate_points:6,financial_available_max:8,
   valuation_candidate_points:3.5,valuation_available_max:5,
   combined_provisional_points:9.5,combined_available_max:13}]
};
assert.equal(unified.applyBulkFinancialPreview(display,bulkParts),1);
assert.equal(unified.pillarState(funded)[2].candidate,6);
assert.equal(unified.pillarState(funded)[2].available,8);
assert.equal(unified.pillarState(funded)[3].candidate,3.5);
assert.equal(unified.pillarState(funded)[3].available,5);
assert.equal(unified.pillarState(funded)[0].available,0);
assert.equal(unified.researchComposite(funded).earned,32.3);
assert.equal(unified.researchComposite(funded).available,38);
// Actual same-filing OpenDART FCF qualifies extra 4/5 subfactor points,
// never a new full 15pt financial/valuation score or a 100pt certification.
const fcfRow={
 code:funded.code,market:funded.market,asof:'2026-10-20',
 historical_PIT_certified:false,scoring_certified:false,
 strict_receipt_scope_matched:true,
 raw_observed_financial_fcf_assets_3y_pct:3.12,
 raw_observed_valuation_fcf_yield_pct:-1.75,
 financial_fcf_assets_candidate_points:2,
 financial_fcf_assets_available_max:4,financial_fcf_peer_count:33,
 valuation_fcf_yield_candidate_points:4,
 valuation_fcf_yield_available_max:5,valuation_fcf_peer_count:35
};
const fcf={
 schema:'v3-actual-dart-fcf-raw-feature-and-peer-preview-0.1',
 asof:'2026-10-20',listed_total:2497,
 source_same_filing_scope_receipt_checked:true,raw_source_rows_stored:false,
 hist_PIT_verified:false,four_year_financial15_complete:false,
 valuation15_complete:false,joined_to_main_v3_ranking:false,
 buy_recommendations_authorized:false,six_pillar_confirmed_100pt:null,
 minimum_same_market_peers:30,financial_fcf_weight_available_max:4,
 valuation_fcf_weight_available_max:5,
 real_fcf_assets_three_year_raw_issuer_count:1,
 real_latest_fcf_yield_raw_issuer_count:1,
 financial_fcf4_research_candidate_issuer_count:1,
 valuation_fcf5_research_candidate_issuer_count:1,
 rows:[fcfRow]
};
display.marketEligible=2497;
assert.equal(unified.applyActualCashflowPreview(display,fcf),1);
assert.equal(unified.pillarState(funded)[2].candidate,8);
assert.equal(unified.pillarState(funded)[2].available,12);
assert.equal(unified.pillarState(funded)[3].candidate,7.5);
assert.equal(unified.pillarState(funded)[3].available,10);
assert.equal(unified.pillarState(funded)[0].candidate,null);
assert.equal(unified.researchComposite(funded).earned,38.3);
assert.equal(unified.researchComposite(funded).available,47);
assert.equal(unified.researchComposite(funded).complete,false);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,asof:'2026-10-21'}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[fcfRow,fcfRow]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[{...fcfRow,financial_fcf_assets_candidate_points:7}]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[{...fcfRow,valuation_fcf_peer_count:29}]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,rows:[{...fcfRow,scoring_certified:true}]}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,financial_fcf4_research_candidate_issuer_count:2}),0);
assert.equal(unified.applyActualCashflowPreview(display,{
 ...fcf,buy_recommendations_authorized:true}),0);
assert.ok(source.includes('applyActualCashflowPreview(model,actualCashflowCoverage)'));
// A missing factor stays null, rather than being assigned zero points.
const unobserved={...funded,v3_dart_fcf_research:{
 ...fcfRow,financial_fcf_assets_candidate_points:null,
 financial_fcf_assets_available_max:0,
 valuation_fcf_yield_candidate_points:null,
 valuation_fcf_yield_available_max:0}};
assert.equal(unified.pillarState(unobserved)[2].available,8);
assert.equal(unified.pillarState(unobserved)[3].available,5);
const forged={...bulkParts,rows:[{...bulkParts.rows[0],
  financial_candidate_points:15,financial_available_max:8}]};
assert.equal(unified.applyBulkFinancialPreview(display,forged),0,
 'overweight financial research score should be blocked');
assert.equal(unified.applyBulkFinancialPreview(display,
 {...bulkParts,asof:'2026-10-21'}),0);
assert.equal(unified.applyBulkFinancialPreview(display,
 {...bulkParts,all_scores_provisional_unverified:false}),0);
assert.equal(unified.applyBulkFinancialPreview(display,
 {...bulkParts,rows:[bulkParts.rows[0],bulkParts.rows[0]]}),0,
 'a duplicate score row should invalidate the whole import');

const savedCoverage=JSON.parse(fs.readFileSync(
 path.join(__dirname,'../docs/v3-real-source-six-area-coverage.json'),'utf8'));
const actualSixText=unified.realSourceCoverageText(
 savedCoverage,savedCoverage.market_asof);
assert.ok(actualSixText.includes('6영역 계산가능 연구점수'));
assert.ok(actualSixText.includes('4영역 데이터 검증 시험군'));
assert.ok(actualSixText.includes('공식 투자자별 수급 0'));
assert.ok(actualSixText.includes(
 '재무 '+savedCoverage.six_pillar_numeric_provisional_issuer_counts.financial));
assert.equal(unified.realSourceCoverageText(
 savedCoverage,'2026-10-19'),'','old/future coverage source cannot blend into a market day');
assert.equal(unified.realSourceCoverageText(
 {...savedCoverage,six_pillar_official_complete_100pt_issuer_count:1},
 savedCoverage.market_asof),'','fabricated full V3 availability is rejected');
assert.equal(unified.realSourceCoverageText(
 {...savedCoverage,six_pillar_numeric_provisional_issuer_counts:{
  ...savedCoverage.six_pillar_numeric_provisional_issuer_counts,ias:25}},
 savedCoverage.market_asof),'','IAS cannot be inferred from other research evidence');
assert.equal(unified.realSourceCoverageText(null,savedCoverage.market_asof),'');
// All currently saved volume/OBV observations are displayed ONLY when the
// full current-listing asof, source counts, issuer markets and bounded
// non-certification are consistent. No artificial foreign net buying.
const volumeSaved=JSON.parse(fs.readFileSync(
 path.join(__dirname,'../docs/v3-volume-obv-observed.json'),'utf8'));
const volumeActual=JSON.parse(fs.readFileSync(
 path.join(__dirname,'../docs/data.json'),'utf8'));
const prepared=legacy.prepare(structuredClone(volumeActual),null,null,null,null,null);
assert.equal(unified.applyObservedVolume(prepared,volumeSaved),
 volumeSaved.observed_volume_candidate_issuers);
assert.ok(prepared.rows.some(x=>unified.pillarState(x.r)[0].available===16));
assert.ok(prepared.rows.every(x=>unified.pillarState(x.r)[0].available<=16));
assert.ok(prepared.rows.every(x=>unified.pillarState(x.r)[0].candidate===null||
  unified.pillarState(x.r)[0].candidate<=16));
const beforeScores=unified.summary(prepared,legacy).provisional;
assert.ok(beforeScores>=volumeSaved.observed_volume_candidate_issuers);
const badCount={...volumeSaved,observed_volume_candidate_issuers:9999};
const second=legacy.prepare(structuredClone(volumeActual),null,null,null,null,null);
assert.equal(unified.applyObservedVolume(second,badCount),0);
assert.ok(second.rows.every(x=>unified.pillarState(x.r)[0].available===0));
const badBuyer=structuredClone(volumeSaved);
const one=Object.keys(badBuyer.records)[0];
badBuyer.records[one].foreign_institutional_net_buy_inferred=true;
const third=legacy.prepare(structuredClone(volumeActual),null,null,null,null,null);
assert.equal(unified.applyObservedVolume(third,badBuyer),0);
assert.ok(third.rows.every(x=>unified.pillarState(x.r)[0].available===0));


const actual=JSON.parse(fs.readFileSync(path.join(__dirname,'../docs/data.json'),'utf8'));
const dailyModel=legacy.prepare(actual,null,null,null,null,null);
const actualAll=unified.rankItems(dailyModel,legacy,'all');
assert.equal(actualAll.length,dailyModel.rows.length);
assert.ok(actualAll.every(x=>x.r.score>=0&&x.r.score<=100));
assert.ok(actualAll.every(x=>x.tech===null||x.tech<=25));
assert.equal(unified.summary(dailyModel,legacy).officialFullScore,0);
// This file is now published by the zero-provider GitHub workflow. Verify
// the ACTUAL production source, not merely an invented synthetic fixture.
const livePrice=JSON.parse(fs.readFileSync(
 path.join(__dirname,'../docs/v3-current-price-technical-momentum.json'),'utf8'));
const freshModel=legacy.prepare(structuredClone(actual),null,null,null,null,null);
assert.equal(livePrice.asof,freshModel.date);
assert.equal(livePrice.priced_current_session,freshModel.rows.length);
assert.ok(livePrice.technical_research_issuer_count>0);
assert.ok(livePrice.momentum_research_issuer_count>0);
const mergedPrice=unified.applyCurrentPriceReplay(freshModel,livePrice);
assert.ok(mergedPrice>0,'real source-only price replay must attach some observed candidates');
assert.equal(freshModel.rows.filter(x=>
 unified.momentumEvidence(x.r)!==null).length,
 livePrice.momentum_research_issuer_count,
 'the same-date real momentum8 evidence must be shown, not silently dropped');
assert.equal(freshModel.rows.filter(x=>
 unified.technicalEvidence(x.r)!==null).length,
 livePrice.technical_research_issuer_count,
 'the real technical source must be visible on each actually observed ticker');
const freshPrior=unified.summary(freshModel,legacy).provisional;
assert.ok(freshPrior>=livePrice.at_least_one_price_factor_issuer_count);
assert.equal(unified.summary(freshModel,legacy).officialFullScore,0);
const badReal=structuredClone(livePrice);
const realKey=Object.keys(badReal.rows)[0];
badReal.rows[realKey].official_investment_score_certified=true;
const otherMarket=legacy.prepare(structuredClone(actual),null,null,null,null,null);
assert.equal(unified.applyCurrentPriceReplay(otherMarket,badReal),0,
 'tampered real record must fail close before ANY issuer gets boosted');

// Actual same-day CSV has complete real financial/value observed points.
// Reconcile every listed code and available weight before attaching ANY row.
const observedPartialSummary=JSON.parse(fs.readFileSync(
 path.join(__dirname,'../docs/v3-current-observed-all-listed-summary.json'),'utf8'));
const observedPartialCsv=fs.readFileSync(
 path.join(__dirname,'../docs/v3-current-observed-all-listed.csv'),'utf8');
assert.ok(source.includes("'v3-current-observed-all-listed-summary.json'"));
assert.ok(source.includes('v3-current-observed-all-listed.csv?v='));
assert.equal(observedPartialSummary.asof,freshModel.date);
assert.equal(observedPartialSummary.listed_total,freshModel.marketEligible);
assert.ok(html.includes('id="fiveAreaCoverage"'));
assert.ok(entry.includes('id="fiveAreaCoverage"'));
assert.ok(css.includes('.u-coverage-scroll'));
const actualCoverage=unified.fiveAreaCoverageView(
 observedPartialSummary,observedPartialSummary.asof,observedPartialSummary.listed_total);
assert.ok(actualCoverage?.includes('5개 영역 실제 데이터 확보'));
assert.ok(actualCoverage.includes('산업은 배점에서 제외됩니다'));
assert.ok(actualCoverage.includes(
 observedPartialSummary.highest_currently_observable_weight+'/100점'));
const forgedCoverage=structuredClone(observedPartialSummary);
forgedCoverage.observed_issuer_counts.financial++;
assert.equal(unified.fiveAreaCoverageView(
 forgedCoverage,forgedCoverage.asof,forgedCoverage.listed_total),null);
const mismatchedCoverage=structuredClone(observedPartialSummary);
mismatchedCoverage.factor_observed_weight_ceiling.financial++;
assert.equal(unified.fiveAreaCoverageView(
 mismatchedCoverage,mismatchedCoverage.asof,mismatchedCoverage.listed_total),null);
const allegedIndustry=structuredClone(observedPartialSummary);
allegedIndustry.observed_issuer_counts.industry=10;
assert.equal(unified.fiveAreaCoverageView(
 allegedIndustry,allegedIndustry.asof,allegedIndustry.listed_total),null);
assert.equal(unified.fiveAreaCoverageView(
 observedPartialSummary,'1900-01-01',observedPartialSummary.listed_total),null);

const overlayCount=unified.applyCurrentObservedFinance(
 freshModel,observedPartialSummary,observedPartialCsv);
assert.ok(overlayCount>=0&&
 overlayCount<=observedPartialSummary.observed_issuer_counts.financial);
if(observedPartialSummary.observed_issuer_counts.financial>0){
 assert.ok(overlayCount>0,'same-date verified research should be displayed');
 const financialRow=freshModel.rows.find(x=>
  x.r.v3_same_day_observed_finance?.financial?.available>0);
 assert.ok(financialRow);
 const sourceFinance=financialRow.r.v3_same_day_observed_finance.financial;
 const displayedFinance=unified.pillarState(financialRow.r).find(x=>x.key==='financial');
 assert.equal(displayedFinance.available,sourceFinance.available);
 assert.equal(displayedFinance.candidate,sourceFinance.points);
 assert.equal(displayedFinance.status,'미검증 연구점수');
}else{
 assert.equal(overlayCount,0,'no same-date DART must leave finance missing');
 assert.ok(freshModel.rows.every(x=>unified.pillarState(x.r)[2].available===0));
}
assert.equal(unified.summary(freshModel,legacy).officialFullScore,0);
const badOverlayModel=legacy.prepare(structuredClone(actual),null,null,null,null,null);
const futureObserved={...observedPartialSummary,
 asof:new Date(Date.parse(freshModel.date+'T00:00:00Z')+86400000)
  .toISOString().slice(0,10)};
assert.equal(unified.applyCurrentObservedFinance(
 badOverlayModel,futureObserved,observedPartialCsv),0);
assert.equal(unified.applyCurrentObservedFinance(
 badOverlayModel,observedPartialSummary,
 observedPartialCsv.replace('미검증 연구용','확정 투자등급')),0);
assert.ok(badOverlayModel.rows.every(x=>
 x.r.v3_same_day_observed_finance===undefined),
 'reject an entire source batch on any mismatched row');

console.log('Unified workspace: V3 provisional sum, missing-data safety, refreshed research scoring, mobile source handling, real-market compatibility PASS; records='+actualAll.length);
