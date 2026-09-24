# -*- coding: utf-8 -*-
import requests

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.cninfo.com.cn/',
})

tests = [
    ('POST', 'https://www.cninfo.com.cn/new/data/szse_stock', {}),
    ('POST', 'https://www.cninfo.com.cn/new/data/szse_stock/query', {'page': '1', 'size': '10'}),
    ('POST', 'https://www.cninfo.com.cn/new/data/sse_stock/query', {'page': '1', 'size': '10'}),
    ('GET', 'https://www.cninfo.com.cn/api-cloud-server/api/query/dataset', None),
    ('GET', 'https://www.cninfo.com.cn/new/data/index', None),
]
for method, url, data in tests:
    try:
        if method == 'POST':
            r = s.post(url, data=data, timeout=20)
        else:
            r = s.get(url, timeout=20)
        print('=' * 50)
        print(method, url, '->', r.status_code)
        print(r.text[:400])
    except Exception as e:
        print('ERR', url, e)
