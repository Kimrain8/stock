# -*- coding: utf-8 -*-
import requests

r = requests.get('https://www.cninfo.com.cn/', timeout=20)
r.encoding = 'utf-8'
open('cninfo_index.html', 'w', encoding='utf-8').write(r.text)
print(r.status_code, len(r.text))
