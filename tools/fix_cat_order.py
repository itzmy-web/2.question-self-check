# -*- coding: utf-8 -*-
"""修复：猫组件 HTML 必须在主 <script> 之前（否则脚本运行时找不到 catDock，猫全挂）。"""
import re

P = r'D:\query\index.html'
h = open(P, encoding='utf-8').read()

MARK = '<!-- ============ AI 客服：可拖动的 3D 暹罗猫「小暹」 ============ -->'
i = h.index(MARK)
j = h.index('</body>', i)
block = h[i:j]
h = h[:i] + h[j:]                      # 从尾部移除
h = h.replace('<script>', block + '<script>', 1)   # 插到主脚本之前
open(P, 'w', encoding='utf-8').write(h)

# 验证顺序
a = h.index(MARK)
b = h.index('<script>', a)
print('cat html pos:', a, '| script pos:', b, '| cat before script:', a < b)
print('no leftover at tail:', MARK not in h[a + len(MARK):])
