# -*- coding: utf-8 -*-
import requests
import json

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.cninfo.com.cn/',
})

# topSearch query used by the homepage search box
try:
    r = s.post('https://www.cninfo.com.cn/new/information/topSearch/query',
               data={'keyWord': '000001', 'maxNum': '10'}, timeout=20)
    print('topSearch:', r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False)[:2000])
except Exception as e:
    print('topSearch err', e)
