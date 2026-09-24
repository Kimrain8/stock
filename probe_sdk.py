# -*- coding: utf-8 -*-
import requests
import json

# Based on sdkSZSE.js GService.Path: tenantLogin: "signthirdparty/login/tenantLogin"
# httpHostName: datasdk.mktdata.cn  (SZSE production)
base = 'https://datasdk.mktdata.cn'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.cninfo.com.cn/',
    'Content-Type': 'application/json',
}

tests = [
    ('/application/signthirdparty/login/tenantLogin', {'tenant': 'cninfo', 'client': 'cninfo'}),
    ('/signthirdparty/login/tenantLogin', {'tenant': 'cninfo'}),
    ('/application/', {}),
]
for path, payload in tests:
    url = base + path
    try:
        if payload:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
        else:
            r = requests.get(url, headers=headers, timeout=20)
        print('=' * 50)
        print(url, '->', r.status_code)
        print(r.text[:500])
    except Exception as e:
        print('ERR', url, e)
