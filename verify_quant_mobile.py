"""Verify deployed v2.1 from a mobile viewport, including real Excel downloads."""
from pathlib import Path
from zipfile import ZipFile
import json
from playwright.sync_api import sync_playwright
URL='https://ykvic1.github.io/korean-stock-screener/'
OUT=Path('verification');OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
 b=p.chromium.launch(headless=True)
 c=b.new_context(viewport={'width':390,'height':844},device_scale_factor=2,is_mobile=True,has_touch=True,accept_downloads=True)
 page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(URL+'quant.html',wait_until='domcontentloaded',timeout=60000)
 page.wait_for_function("/^[0-9]+$/.test(document.getElementById('count').textContent)",timeout=30000)
 q=c.request.get(URL+'quant.json').json()
 assert q['status']=='exploratory' and q['bias_audit']['prediction_validated'] is False
 assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'),'horizontal overflow'
 stocks=q['stocks'];s=next(x for x in stocks if x.get('periods'))
 page.get_by_role('textbox',name='검색',exact=True).fill(s['code'])
 page.get_by_role('combobox',name='종목 선택').select_option(s['code'])
 assert page.locator('#rows tr').count()==7
 assert s['name'] in page.locator('#pick').inner_text()
 for period in s['periods'].values():
  if period['status']=='insufficient':assert 'up_pct' not in period
  else:
   assert period['sample_n']>=40 and period['symbol_n']>=10 and period['quarter_n']>=12
   assert abs(period['up_pct']+period['down_or_flat_pct']-100)<=.2
 for element,file in [('excel','quant-mobile.xlsx'),('combined','combined-mobile.xlsx')]:
  with page.expect_download(timeout=30000) as d:page.locator('#'+element).click()
  d.value.save_as(str(OUT/file))
  with ZipFile(OUT/file) as z:assert z.testzip() is None
 page.screenshot(path=str(OUT/'quant-mobile.png'),full_page=True)
 page.get_by_role('textbox',name='검색',exact=True).fill('__none__')
 assert page.locator('#select option').count()==1
 assert not errors,errors
 (OUT/'quant-result.json').write_text(json.dumps({'url':page.url,'viewport':'390x844','quant_stocks':len(stocks),'available_symbols':q['available_symbols'],'checks':['7 horizons','sample gating','name/code filter','full-horizon statistics','both Excel downloads','no page errors','no horizontal overflow']},ensure_ascii=False,indent=2))
 b.close()
