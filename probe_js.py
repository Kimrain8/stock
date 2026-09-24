# -*- coding: utf-8 -*-
import re

h = open('stock_page.html', encoding='utf-8').read()
for m in re.findall(r'src="([^"]+\.js[^"]*)"', h):
    print(m)
print('---STYLE---')
for m in re.findall(r'href="([^"]+\.css[^"]*)"', h):
    print(m)
