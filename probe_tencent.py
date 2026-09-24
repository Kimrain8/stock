# -*- coding: utf-8 -*-
import requests

# 腾讯行情接口，GBK 编码
url = "https://qt.gtimg.cn/q=sh600519,sz000001,sz300750,bj920000"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}
try:
    r = requests.get(url, headers=headers, timeout=15)
    print('status', r.status_code)
    lines = r.text.strip().split(';')
    for line in lines:
        if '~' in line:
            parts = line.split('~')
            if len(parts) > 50:
                # 常见字段: 1名称 2代码 3现价 38换手率 49量比
                print('name:', parts[1], 'code:', parts[2], 'price:', parts[3],
                      '换手率(idx38):', parts[38], '量比(idx49):', parts[49])
except Exception as e:
    print('ERR', repr(e))
