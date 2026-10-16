"""Manual smoke check of the deployed Pages site in a mobile Chromium viewport."""
from pathlib import Path
from zipfile import ZipFile
import json
import os
import re
from mobile_smoke_contract import check_flow_filter
from playwright.sync_api import sync_playwright

URL='https://ykvic1.github.io/korean-stock-screener/'
OUT=Path('verification');OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=2,is_mobile=True,has_touch=True,accept_downloads=True)
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(URL,wait_until='domcontentloaded',timeout=60000)
    page.locator('article.stock').first.wait_for(timeout=30000)
    data=context.request.get(URL+'data.json').json()
    assert data['version']=='2.0' and data['status']=='success'
    assert data['candidate_count']==len(data['stocks'])
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
    stocks=page.locator('article.stock');total=stocks.count()
    name=stocks.first.locator('.name').inner_text().split('. ',1)[1]
    code=stocks.first.locator('.info').first.inner_text().split(' · ')[0]
    search=page.get_by_role('textbox',name='종목 검색')
    search.fill(code);assert stocks.count()==1
    search.fill(name);assert stocks.count()>=1
    search.fill('__unknown_stock__');assert stocks.count()==0
    assert page.get_by_text('이 조건의 종목이 없습니다. 다른 필터를 선택하세요.',exact=True).is_visible()
    search.fill('')
    page.get_by_role('button',name='수급 확인',exact=True).click()
    flow_gate = check_flow_filter(data, stocks.count())
    if flow_gate['reported'] == 0:
        # UI must remain honest when live investor flows were not collected.
        assert page.get_by_text('이 조건의 종목이 없습니다. 다른 필터를 선택하세요.',exact=True).is_visible()
        assert page.locator('#flowCount').inner_text().strip() == '0'
        assert 'bad' in (page.locator('#status').get_attribute('class') or '').split()
    page.get_by_role('button',name='전체',exact=True).click();assert stocks.count()==total
    for label in ['상승 초기','차트 패턴','주봉 전환','거래량 신호','OBV 괴리','손절폭 15% 이내']:
        page.get_by_role('button',name=label,exact=True).click()
        assert stocks.count()<=total
    for text in stocks.all_inner_texts():
        m=re.search(r'현재가 대비 (-?[\d.]+)%',text)
        assert m and abs(float(m.group(1)))<=15
    page.get_by_role('button',name='전체',exact=True).click()
    for sort in ['risk','recent','relative','coverage','score']:
        page.get_by_role('combobox',name='종목 정렬').select_option(sort)
        assert stocks.count()==total
    # Current production UI intentionally displays unconfirmed scores as '미확정',
    # not as a numerical ranking with verified investment predictive power.
    labels=page.locator('article.stock .score').all_inner_texts()
    assert len(labels)==total and all(x.startswith('미확정') for x in labels)
    expected_codes=[r['code'] for r in sorted(data['stocks'],
        key=lambda r:r['score'] if isinstance(r.get('score'),(float,int)) else -1,
        reverse=True)]
    rendered_codes=[x.split(' · ',1)[0] for x in
        page.locator('article.stock .info').all_inner_texts()[::2]]
    assert rendered_codes==expected_codes, 'Legacy research sort order mismatch'
    for row in data['stocks']:
        valid={k:v for k,v in row['components'].items() if v is not None}
        expected=round(100*sum(valid.values())/sum(data['weights'][k] for k in valid),1)
        assert abs(row['score']-expected)<0.11
        if not row['flows_confirmed']:
            assert row['foreign_shares'] is None and row['inst_shares'] is None
    with page.expect_download(timeout=30000) as d:
        page.get_by_role('link',name='↓ 최신 엑셀 받기',exact=True).click()
    download=d.value;download.save_as(str(OUT/'mobile-download.xlsx'))
    with ZipFile(OUT/'mobile-download.xlsx') as z:
        assert z.testzip() is None and 'xl/workbook.xml' in z.namelist()
    search.fill(code)
    page.get_by_text('수급 · 차트 · 상대강도 · 위험 상세 보기',exact=True).click()
    page.screenshot(path=str(OUT/'mobile-verified.png'),full_page=True)
    assert not errors,errors
    result={
        'url':page.url,
        'viewport':'390x844, Chromium mobile emulation',
        'mobile_ui_verification':'passed',
        'candidates':total,
        'flows':data['flow_confirmed_count'],
        'flow_quality':flow_gate,
        'data_quality':'blocked_pending_KRX_session_and_source_verification',
        'errors_in_collection':data['error_count'],
        'checks':['name/code search','empty result','8 filters','5 sorts',
                  'unconfirmed score labels and legacy sort',
                  'legacy research arithmetic','unknown flow values',
                  'Excel download','no horizontal overflow','no page JS errors'],
    }
    if flow_gate['reported']==0:
        warning='수급 확인 성공 0건: 모바일 UI 시험 통과와 무관하게 투자용 수급 데이터는 미확인입니다.'
        print('::warning title=Investor flow data unavailable::'+warning)
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary,'a',encoding='utf-8') as report:
                report.write('### 모바일 UI 검증과 데이터 검증 구분\n\n'
                             '- 모바일 화면: 기본 조작 검증 통과\n'
                             '- **외국인·기관 수급: 0건, 데이터 품질 미검증**\n'
                             '- KRX 정규장 OHLCV/독립 공급원: 미검증\n')
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    browser.close()
