const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'docs/v3-fcf-gap.js'), 'utf8');
const gap = JSON.parse(fs.readFileSync(path.join(root, 'docs/v3-dart-cashflow-gap-summary.json'), 'utf8'));
const live = JSON.parse(fs.readFileSync(path.join(root, 'docs/v3-current-observed-all-listed-summary.json'), 'utf8'));

async function render(data) {
  const el = {textContent: ''};
  const document = {getElementById(id) {return id === 'fcfMissingness' ? el : null;}};
  const fetch = async url => ({ok:true,json:async()=> url.includes('gap-summary') ? data : live});
  vm.runInNewContext(source, {document,fetch});
  for(let i=0;i<10;i++) await new Promise(resolve=>setImmediate(resolve));
  return el.textContent;
}
test('FCF annual breakdown uses real saved company-year observations', async () => {
  const output = await render(gap);
  for (const year of ['2023','2024','2025']) assert.match(output, new RegExp(year + '년 확인'));
  assert.match(output,/없는 계정은 0원으로 처리하지 않으며/);
  assert.match(output,/투자점수 인증도 아닙니다/);
});
test('mismatched annual categories fail closed rather than inventing values', async () => {
  const bad = structuredClone(gap);
  bad.by_year['2025'].missing_ppe_only += 1;
  const output = await render(bad);
  assert.match(output,/표시를 보류합니다/);
  assert.doesNotMatch(output,/2025년 확인/);
});
