# -*- coding: utf-8 -*-
"""Add emoji favicon to login.html / index.html in all 3 copies (idempotent)."""
import re

FAV = ('<link rel="icon" href="data:image/svg+xml,'
       "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
       "<text y='.9em' font-size='90'>\U0001F4DD</text></svg>\">")

DIRS = [
    r'D:\query',
    r'C:\Users\zmy\Desktop\问题自检项目',
    r'C:\Users\zmy\WorkBuddy\2026-09-28-15-29-59\self-check-quiz',
]

for d in DIRS:
    for f in ('login.html', 'index.html'):
        p = d + '\\' + f
        with open(p, encoding='utf-8') as fh:
            html = fh.read()
        if 'rel="icon"' in html:
            print('skip (has icon):', p)
            continue
        m = re.search(r'<title>.*?</title>', html)
        if not m:
            print('NO TITLE:', p)
            continue
        html2 = html[:m.end()] + '\n' + FAV + html[m.end():]
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(html2)
        print('favicon added:', p)
