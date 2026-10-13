# -*- coding: utf-8 -*-
"""三个细节优化：①标题蓝字改文案 ②客服对话多条并发 ③摸摸/跳跳/喵喵真动画。"""
P = r'D:\query\index.html'
h = open(P, encoding='utf-8').read()

# 1) 标题蓝字文案
old1 = '<small>项目问题 · 闲聊都接</small>'
assert old1 in h, 'title tag not found'
h = h.replace(old1, '<small>小猫客服，在线服务</small>', 1)

# 2) 对话并发：去掉发送按钮禁用逻辑（允许多条问题同时等待回答）
old2 = "  const thinking=botSay('小暹思考中…');\n  $('catSend').disabled=true;"
assert old2 in h, 'send lock not found'
h = h.replace(old2, "  const thinking=botSay('小暹思考中…');", 1)
old3 = "  }finally{ $('catSend').disabled=false; }"
assert old3 in h, 'send unlock not found'
h = h.replace(old3, "  }", 1)
# 思考中的气泡加旋转指示
old4 = "function botSay(t){const m=document.createElement('div');m.className='cat-msg bot';m.textContent=t;"
assert old4 in h
h = h.replace(old4, "function botSay(t){const m=document.createElement('div');m.className='cat-msg bot'+(t==='小暹思考中…'?' loading':'');m.textContent=t+(t==='小暹思考中…'?' ⏳':'');", 1)

# 3) 动作真动画：替换三个 handler
old5 = """catBubble.querySelectorAll('button').forEach(b=>b.onclick=()=>{
  const act=b.dataset.act;
  if(act==='pet'){ catFast=true; setTimeout(()=>catFast=false,1200); toast('小暹：呼噜呼噜～被摸得好舒服喵！'); }
  else if(act==='happy'){ catFast=true; setTimeout(()=>catFast=false,1200); toast('小暹：喵呜！开心得书都翻快啦～'); }
  else { toast('小暹：喵喵喵！有问题点我说，没事也可以撸猫～'); }
});"""
assert old5 in h, 'handlers not found'
new5 = """function catAnim(cls,ms){ catDock.classList.remove(cls); void catDock.offsetWidth; catDock.classList.add(cls); setTimeout(()=>catDock.classList.remove(cls),ms); }
function spawnHearts(){
  const r=catPos();
  for(let i=0;i<6;i++){
    const s=document.createElement('span'); s.className='cat-heart';
    s.textContent=['❤️','💕','✨','💙'][i%4];
    s.style.left=(r.left+12+Math.random()*(r.width-24))+'px';
    s.style.top=(r.top-4-Math.random()*10)+'px';
    s.style.animationDelay=(i*0.09)+'s';
    document.body.appendChild(s); setTimeout(()=>s.remove(),1500);
  }
}
function catSay(t){
  const r=catPos();
  const old=document.querySelector('.cat-say'); if(old) old.remove();
  const s=document.createElement('div'); s.className='cat-say'; s.textContent=t;
  s.style.left=Math.max(8,r.left-60)+'px'; s.style.top=Math.max(8,r.top-46)+'px';
  document.body.appendChild(s); setTimeout(()=>s.remove(),2300);
}
catBubble.querySelectorAll('button').forEach(b=>b.onclick=()=>{
  const act=b.dataset.act;
  catFast=true; setTimeout(()=>catFast=false,1400);
  if(act==='pet'){ catAnim('wiggling',650); spawnHearts(); catSay('呼噜呼噜～最开心被摸头了喵！'); }
  else if(act==='happy'){ catAnim('jumping',800); catSay('喵呜！看我 leap～'); }
  else { catAnim('talking',550); catSay('喵喵喵～有问题尽管问！'); }
});"""
h = h.replace(old5, new5, 1)

# 4) 新动画 CSS（加在 .cat-dock img.on 之后）
old6 = "  .cat-dock img.on{display:block}"
assert old6 in h
h = h.replace(old6, """  .cat-dock img.on{display:block}
  .cat-dock.jumping{animation:catRealJump .8s cubic-bezier(.3,0,.25,1)}
  @keyframes catRealJump{0%,100%{transform:translateY(0)}30%{transform:translateY(-46px) rotate(-4deg)}
    55%{transform:translateY(-6px)}75%{transform:translateY(-22px)}}
  .cat-dock.wiggling{animation:catWiggle .65s ease-in-out}
  @keyframes catWiggle{0%,100%{transform:rotate(0)}25%{transform:rotate(-9deg)}55%{transform:rotate(8deg)}80%{transform:rotate(-4deg)}}
  .cat-dock.talking{animation:catTalk .55s ease}
  @keyframes catTalk{0%,100%{transform:translateX(0)}25%{transform:translateX(-5px)}55%{transform:translateX(5px)}80%{transform:translateX(-3px)}}
  .cat-heart{position:fixed;z-index:98;font-size:15px;pointer-events:none;opacity:0;
    animation:heartUp 1.3s ease-out forwards}
  @keyframes heartUp{0%{opacity:1;transform:translateY(0) scale(.6)}100%{opacity:0;transform:translateY(-60px) scale(1.3)}}
  .cat-say{position:fixed;z-index:98;white-space:nowrap;background:#fffaf4;border:2px solid #c99a5f;
    border-radius:12px;padding:5px 12px;font-size:12.5px;font-weight:700;color:#7a4a21;
    box-shadow:0 6px 16px rgba(0,0,0,.22);animation:popIn .22s ease}
  @keyframes popIn{0%{opacity:0;transform:scale(.6)}100%{opacity:1;transform:scale(1)}}
  .cat-msg.loading{color:var(--muted);font-style:italic}""", 1)

open(P, 'w', encoding='utf-8').write(h)
print('all 4 edits applied')
