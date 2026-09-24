# -*- coding: utf-8 -*-
import requests
import json

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.cninfo.com.cn/',
})

endpoints = [
    ('https://www.cninfo.com.cn/data/centerSpecial/marketOverview', None),
    ('https://www.cninfo.com.cn/data/centerSpecial/getIndexStatistics', None),
    ('https://www.cninfo.com.cn/data/centerSpecial/capitalFlows', None),
    ('https://www.cninfo.com.cn/new/index/loadTbTrade', None),
]
for url, _ in endpoints:
    try:
        r = s.post(url, timeout=20)
        txt = r.text[:600]
        print('=' * 60)
        print('POST', url, '->', r.status_code)
        print(txt)
    except Exception as e:
        print('ERR', url, e)
