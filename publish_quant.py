"""Append quant sheets to a NEW daily workbook; retain all old reports unchanged."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json, shutil
from openpyxl import load_workbook
from quant_excel import create
from official_data import collect

site=Path('docs');now=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%H%M%S')
d=json.loads((site/'quant.json').read_text());stamp=d['asof']+'_'+now
create(site/'quant.json',Path('reports/quant_study.xlsx'))
collect()
latest=site/'reports/latest.xlsx'
wb=load_workbook(latest)
q=load_workbook('reports/quant_study.xlsx')
for sheet in q:
    title='퀀트_'+sheet.title
    if title in wb:del wb[title]
    dest=wb.create_sheet(title)
    for row in sheet.iter_rows(values_only=True):dest.append(row)
    dest.freeze_panes='A2'
    for k,v in sheet.column_dimensions.items():dest.column_dimensions[k].width=v.width
fund=json.loads((site/'official.json').read_text());ws=wb.create_sheet('공식데이터상태') if '공식데이터상태' not in wb else wb['공식데이터상태']
ws.append(['자료','상태','비고']);ws.append(['DART',fund['dart']['status'],'공시 접수일 제한, 역사 백테스트 미반영']);ws.append(['KRX',fund['krx']['status'],'업종지수 인증 필요, 과거 구성종목 미구현'])
filename=f'한국주식_통합퀀트_{stamp}.xlsx';wb.save(site/'reports'/filename);shutil.copyfile(site/'reports'/filename,latest)
quantfile=f'quant_{stamp}.xlsx';shutil.copyfile('reports/quant_study.xlsx',site/'reports'/quantfile);shutil.copyfile('reports/quant_study.xlsx',site/'reports/quant_latest.xlsx')
archive=site/'archive';archive.mkdir(exist_ok=True);shutil.copyfile(site/'quant.json',archive/f'quant_{stamp}.json')
history=json.loads((site/'history.json').read_text());history.insert(0,{'date':d['asof'],'file':'reports/'+filename});(site/'history.json').write_text(json.dumps(history,ensure_ascii=False,indent=2))
d['report_file']='reports/'+quantfile;d['combined_report_file']='reports/'+filename
(site/'quant.json').write_text(json.dumps(d,ensure_ascii=False,indent=2))
print(json.dumps({'combined_report':filename,'quant_report':quantfile,'status':d['status']},ensure_ascii=False))
