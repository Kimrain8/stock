# -*- coding: utf-8 -*-
import re
import requests

r = requests.get('https://www.cninfo.com.cn/', timeout=20)
r.encoding = 'utf-8'
html = r.text
print('status:', r.status_code, 'len:', len(html))

seen = set()
for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{0,60})</a>', html):
    url, txt = m.group(1), m.group(2).strip()
    if url in seen:
        continue
    seen.add(url)
    if any(k in url.lower() for k in ['data', 'quote', 'market']) or any(
        k in txt for k in ['行情', '数据', '报价', '中心']
    ):
        print(repr(txt), '->', url)
