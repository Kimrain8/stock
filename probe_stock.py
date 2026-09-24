# -*- coding: utf-8 -*-
import requests

r = requests.get('https://www.cninfo.com.cn/new/disclosure/stock?orgId=gssz0000001&stockCode=000001', timeout=20)
r.encoding = 'utf-8'
open('stock_page.html', 'w', encoding='utf-8').write(r.text)
print(r.status_code, len(r.text))
