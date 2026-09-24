# -*- coding: utf-8 -*-
import requests

r = requests.get('https://static.cninfo.com.cn/new/assets/js/sdk/NWeb.js', timeout=30)
text = r.content.decode('utf-8', errors='replace')
open('NWeb.js', 'w', encoding='utf-8').write(text)
print(r.status_code, len(text))
