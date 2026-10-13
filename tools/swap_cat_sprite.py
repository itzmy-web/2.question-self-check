# -*- coding: utf-8 -*-
"""把 index.html 的 SVG 猫替换为视频逐帧精灵动画（16 帧 PNG 序列）。"""
import re

P = r'D:\query\index.html'
h = open(P, encoding='utf-8').read()

# 1) CSS：SVG 动画样式 → 精灵帧样式
pat = re.compile(r'  /\* ---------- AI 客服：可拖动暹罗猫 ---------- \*/.*?(?=  \.cat-bubble\{)', re.S)
new_css = '''  /* ---------- AI 客服：可拖动暹罗猫（视频逐帧精灵动画） ---------- */
  .cat-dock{position:fixed;z-index:95;right:26px;bottom:26px;width:96px;height:110px;cursor:grab;
    user-select:none;-webkit-user-select:none;touch-action:none;
    filter:drop-shadow(0 10px 18px rgba(0,0,0,.28))}
  .cat-dock:active{cursor:grabbing}
  .cat-dock.drag{filter:drop-shadow(0 18px 26px rgba(0,0,0,.36))}
  .cat-dock img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain;
    pointer-events:none;display:none}
  .cat-dock img.on{display:block}
'''
m = pat.search(h)
assert m, 'CSS block not found'
h = h[:m.start()] + new_css + h[m.end():]

# 2) SVG → 16 帧 img 叠放
imgs = '\n'.join(
    '  <img src="assets/cat/f%02d.png" class="%s" alt="AI 客服暹罗猫">' % (i + 1, 'on' if i == 0 else '')
    for i in range(16))
h2 = re.sub(r'<svg viewBox="0 0 100 110"[\s\S]*?</svg>', imgs, h, count=1)
assert h2 != h, 'svg not found'
h = h2

# 3) JS：帧循环播放（互动时加速 = 猫兴奋翻书）
old = "const catDock=$('catDock'),catChat=$('catChat'),catBubble=$('catBubble'),catMsgs=$('catMsgs');\nlet catHistory=[];"
assert old in h, 'js anchor not found'
new = old + """
const catFrames=[...catDock.querySelectorAll('img')];
let catFi=0,catFast=false;
(function catLoop(){
  setTimeout(()=>{
    if(catFrames.length){ catFrames[catFi].classList.remove('on'); catFi=(catFi+1)%catFrames.length; catFrames[catFi].classList.add('on'); }
    catLoop();
  }, catFast?50:110);
})();"""
h = h.replace(old, new, 1)

h = h.replace(
    "if(act==='pet'){ catDock.classList.add('pet'); setTimeout(()=>catDock.classList.remove('pet'),560); toast('小暹：呼噜呼噜～被摸得好舒服喵！'); }",
    "if(act==='pet'){ catFast=true; setTimeout(()=>catFast=false,1200); toast('小暹：呼噜呼噜～被摸得好舒服喵！'); }")
h = h.replace(
    "else if(act==='happy'){ catDock.classList.add('happy'); setTimeout(()=>catDock.classList.remove('happy'),700); toast('小暹：喵呜！跳起来啦～'); }",
    "else if(act==='happy'){ catFast=true; setTimeout(()=>catFast=false,1200); toast('小暹：喵呜！开心得书都翻快啦～'); }")

open(P, 'w', encoding='utf-8').write(h)
print('index.html updated')
