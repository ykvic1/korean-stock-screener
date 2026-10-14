"""Execution/UI contract is separate from v3 data approval."""
import json,hashlib,threading,http.server,os
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    Path('evidence').mkdir(exist_ok=True);a=json.load(open('docs/reliability.json'));q=json.load(open('docs/quant_v22.json'))
    assert len(a['stocks'])==a['requested'];assert len(q['stocks'])==a['requested'];assert q['v3_ready'] is False
    assert sum(s.get('strict_valid',False) for s in a['stocks'])==q['available_symbols']
    for s in q['stocks']:
        for group in ['periods','rsi_periods']:
            for p in s.get(group,{}).values():
                assert sum(p.get('year_distribution',{}).values())==p.get('sample_n',0)
                if p['status']=='insufficient':assert 'strategy_win_pct' not in p
    old=Path('evidence/old_report_hashes.json')
    if old.exists():
        for path,h in json.loads(old.read_text()).items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==h,path
    live=os.getenv('V22_VERIFY_URL')
    if not live:
        server=http.server.ThreadingHTTPServer(('127.0.0.1',8765),http.server.SimpleHTTPRequestHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
    with sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,accept_downloads=True);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(live or 'http://127.0.0.1:8765/docs/reliability.html');page.wait_for_function("document.querySelector('#stock').options.length>0")
        assert page.locator('#stock option').count()==a['requested']
        s=next(s for s in q['stocks'] if s.get('periods'));page.locator('#search').fill(s['code']);assert page.locator('#stock option').count()==1
        assert page.locator('#periods tr').count()==14
        assert page.locator('#rsi').inner_text().find('일봉 RSI14')>=0
        page.locator('#search').fill('ZZZ_NO_MATCH');assert page.locator('#name').inner_text()=='검색 결과 없음';assert page.locator('#periods').inner_text()==''
        page.locator('#search').fill(s['code'])
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        with page.expect_download() as dl:page.get_by_role('link',name='v2.2 날짜별 통합 엑셀').click()
        dl.value.save_as('evidence/v22-mobile.xlsx');assert Path('evidence/v22-mobile.xlsx').stat().st_size>10000
        page.screenshot(path='evidence/v22-mobile.png',full_page=True)
        assert not errors,errors;browser.close()
    result={'execution':'passed','mobile':'passed','raw_fetched':a['fetched'],'strict_valid':a['strict_valid'],'cross_matched':a['independently_matched'],'v3_ready':False,'target':live or 'local-built-site'}
    Path('evidence/verification.json').write_text(json.dumps(result,indent=2));print(result)
if __name__=='__main__':main()
