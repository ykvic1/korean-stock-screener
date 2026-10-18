'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {rating, tenthsTruncate} = require('../docs/partial_score.js');

// Synthetic only: check precisely the user's requested 70/85 -> 82.3.
const weights = {
  '수급': 15,
  '재무': 20,
  '밸류': 15,
  '기술': 20,
  '업종': 15,
  '모멘텀': 15,
};
const observed = {
  '수급': 10, '재무': 18, '밸류': null,
  '기술': 18, '업종': 12, '모멘텀': 12
}; // 70 observed of 85 available.
const x = rating({components:observed}, weights);
assert.equal(x.earned, 70);
assert.equal(x.available, 85);
assert.equal(x.coverage, 85);
assert.equal(x.normalized, 82.3);
assert.deepEqual(x.missing, [{name:'밸류',weight:15}]);
assert.equal(x.provisional, true);
assert.equal(x.scale, 'legacy-v2-observed-only');
assert.equal(tenthsTruncate(70/85*100), 82.3);

// Complete observations: an actual 0pt observation counts in the denominator.
const zero = rating({components:{...observed, '밸류':0}},weights);
assert.equal(zero.available,100);
assert.equal(zero.earned,70);
assert.equal(zero.normalized,70);
assert.deepEqual(zero.missing,[]);

// More missing factors do NOT create fabricated zeros.
const sparse = rating({components:{
  '수급':null, '재무':null,'밸류':null,
  '기술':18,'업종':null,'모멘텀':null
}},weights);
assert.equal(sparse.available,20);
assert.equal(sparse.normalized,90);
assert.equal(sparse.coverage,20);
assert.equal(sparse.missing.length,5);
// High normalized/low coverage must be visibly qualified on website.
const html = fs.readFileSync(path.join(__dirname,'../docs/index.html'),'utf8');
assert.ok(html.includes('확보율이 낮으면 환산점수가 높아도 신뢰하기 어렵습니다'));
assert.ok(html.includes('rating.coverage'));
assert.ok(html.includes('미취득 평가항목'));
assert.ok(html.includes('잠정'));
assert.ok(html.includes('window.PartialScoreView?.rating(row,d.weights)'));
assert.ok(html.includes('partial_score.js'));
assert.ok(html.includes('70점 / 확보배점 85점 = 82.3점'));

// Source error, partial metadata, weights drift => no numeric score.
for(const [row, w] of [
  [{components:Object.fromEntries(Object.keys(weights).map(k=>[k,null]))},weights],
  [{components:{...observed, '밸류':null, '수급':-1}},weights],
  [{components:{...observed, '수급':16}},weights],
  [{components:{...observed, '수급':NaN}},weights],
  [{components:{...observed, '수급':Infinity}},weights],
  [{components:{...observed, '수급':true}},weights],
  [{components:{...observed, '수급':'10'}},weights],
  [{components:{...observed, '수급':undefined}},weights],
  [{components:{...observed, '수급':10}},{...weights,'모멘텀':16}],
  [{components:{...observed, '수급':10}},{...weights,'추가':5}],
  [{components:{...observed}},null],
  [{components:{}},weights],
]) {
  assert.equal(rating(row,w),null,'should not claim a score for invalid/missing contract');
}

// Existing v2 delivered component contract, without any invented financials.
const original = {
  '거래량·OBV':20, '일봉 구조':20, '주봉·추세':15,
  '수급':20, '시장 대비 강도':10, 'PBR':5,
  '실적·현금흐름':5, '위험·손익비':5
};
const example = {
  '거래량·OBV':14, '일봉 구조':15, '주봉·추세':11,
  '수급':null, '시장 대비 강도':6, 'PBR':null,
  '실적·현금흐름':null, '위험·손익비':4
};
const y=rating({components:example},original);
assert.equal(y.earned,50);
assert.equal(y.available,70);
assert.equal(y.normalized,71.4);
assert.equal(y.coverage,70);
assert.deepEqual(y.missing.map(x=>x.name),['수급','PBR','실적·현금흐름']);

// Full market scan remains independent from 20 candidate shortlist.
assert.ok(html.includes('관심 종목 최대 20개'));
console.log('provisional_normalized_score_ui_tests_passed');
