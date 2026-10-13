# -*- coding: utf-8 -*-
"""index.html 前端静态校验（不联网）。

  1. 抽出内联 <script>，交给 node --check 做语法检查
  2. 收集 HTML 里的所有 id，检查重复
  3. 检查 JS 里所有 $('xxx') 引用的 id 都存在
  4. 检查 5 个受保护请求是否都带了访问码头（authHeaders / X-Access-Code）
  5. 检查闸门必需的 5 个元素与关键函数是否都在
  6. 检查页面里没有泄露 API Key
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(os.path.dirname(HERE), "quiz.html")  # 老版抽题应用已迁移到 quiz.html
NODE = r"C:/Users/zmy/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"

src = open(HTML, encoding="utf-8").read()
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <-- {extra}"))


# ---- 1. 抽出 script 并做语法检查 ----
blocks = re.findall(r"<script[^>]*>(.*?)</script>", src, re.S)
check("找到内联 script", len(blocks) >= 1, f"找到 {len(blocks)} 个")
js = "\n".join(blocks)
jsfile = os.path.join(tempfile.mkdtemp(), "inline.js")
open(jsfile, "w", encoding="utf-8").write(js)
p = subprocess.run([NODE, "--check", jsfile], capture_output=True, text=True)
check("JS 语法通过 node --check", p.returncode == 0, (p.stderr or "")[:600])

# ---- 2. id 收集 / 重复检查 ----
ids = re.findall(r'\sid="([^"]+)"', src)
dups = sorted({i for i in ids if ids.count(i) > 1})
check("无重复 id", not dups, f"重复：{dups}")
idset = set(ids)

# ---- 3. $('xxx') 引用是否都存在 ----
refs = set(re.findall(r"\$\('([^']+)'\)", js))
missing = sorted(refs - idset)
check("所有 $('id') 引用都有对应元素", not missing, f"缺失：{missing}")

# ---- 4. 受保护请求是否都带码 ----
# 受保护端点清单：每个 fetch 调用都必须出现在 authHeaders(...) 同一调用里
protected = [
    ("/api/job/", r"fetch\('/api/job/'\+jid,\{headers:authHeaders\(\)"),
    ("/api/extract", r"fetch\('/api/extract',\{method:'POST',headers:authHeaders"),
    ("/api/options", r"fetch\('/api/options',\{method:'POST',headers:authHeaders"),
    ("/api/regen-answer", r"(?:fetch|fetchRetry)\('/api/regen-answer',\{method:'POST',headers:authHeaders"),
    ("/api/asr", r"fetch\('/api/asr',\{method:'POST',headers:authHeaders\(\)"),
    ("/api/clean-text", r"fetch\('/api/clean-text',\{method:'POST',headers:authHeaders"),
    ("/api/doc/upload-chunk", r"(?:fetch|fetchRetry)\('/api/doc/upload-chunk',\{method:'POST',headers:authHeaders\(\)"),
    ("/api/doc/upload-complete", r"(?:fetch|fetchRetry)\('/api/doc/upload-complete',\{method:'POST',headers:authHeaders\(\)"),
]
for name, pat in protected:
    check(f"{name} 带上访问码头", re.search(pat, js, re.S) is not None)
check("access/verify 端点在用", "/api/access/verify" in js)
# 上传走 XHR（FormData），单独验证其请求头里带访问码
check("/api/doc/upload 带上访问码头（XHR）",
      re.search(r"xhr\.open\('POST','/api/doc/upload'\);\s*const ah=authHeaders\(\);"
                r"[^}]*setRequestHeader\(k,ah\[k\]\)", js, re.S) is not None)
check("受保护 fetch 均带 authHeaders",
      all(re.search(r"(?:fetch|fetchRetry)\('" + re.escape(ep) + r"'[^)]*authHeaders", js, re.S)
          for ep in ("/api/job/", "/api/extract", "/api/options", "/api/regen-answer",
                     "/api/asr", "/api/clean-text",
                     "/api/doc/upload-chunk", "/api/doc/upload-complete")),
      re.findall(r"(?:fetch|fetchRetry)\('(/api/[^']*)'", js))

# ---- 5. 闸门必需元素与函数 ----
for i in ("accessGate", "gateInput", "gateErr", "gateBtn", "gateClear"):
    check(f"闸门元素 #{i}", i in idset)
for f in ("getCode", "setCode", "authHeaders", "showGate", "hideGate",
          "needCodeAgain", "submitGate"):
    check(f"函数 {f}()", re.search(rf"function {f}\s*\(", js) is not None)
check("启动流程读 access_required", "d.access_required" in js)
check("门关闭时不弹闸门", re.search(r"if\(!needAccess\)\{hideGate\(\);return;\}", js) is not None)

# ---- 5b. 任务栏 + 历史记录 ----
check("分组标题已改为「任务栏」", ">任务栏<" in src)
check("已无「主流程」字样", "主流程" not in src)
check("历史记录在任务栏中", 'id="stepH"' in src)
for i in ("panelH", "histList", "htabDoc", "htabSes", "histRefresh",
          "histClear", "navBadgeH", "confirmModal", "cfTitle", "cfText",
          "cfOk", "cfCancel", "sumWrap", "partialBox"):
    check(f"新增元素 #{i}", i in idset)
for f in ("askConfirm", "cfClose", "cfOpen", "loadHistory", "renderDocs",
          "renderSessions", "saveDocHistory", "saveSession", "deriveTopic",
          "buildAspects", "summaryHTML", "quizUnfinished", "quizLeft",
          "navGo", "leaveQuizGuard", "resetAll", "loadDocToWorkspace", "viewSession"):
    check(f"新增函数 {f}()", re.search(rf"function {f}\s*\(", js) is not None)

print("\n  -- 历史记录接口调用 --")
for ep in ("/api/history/docs", "/api/history/doc/", "/api/history/sessions",
           "/api/history/session/", "/api/history"):
    check(f"用到 {ep}", ep in js)
check("历史请求带登录 token",
      re.search(r"h\['Authorization'\]='Bearer '\+t", js) is not None)
check("历史请求也带访问码头", re.search(r"return authHeaders\(h\);", js) is not None)

print("\n  -- 退出拦截 --")
check("有 beforeunload 拦截", "beforeunload" in js)
check("退出自检会确认", re.search(r"\$\('quitQuiz'\).onclick=async\(\)=>", js) is not None)
check("退出登录会确认", re.search(r"_lb\.onclick=async\(\)=>", js) is not None)
check("重新开始会确认", re.search(r"\$\('restartTop'\).onclick=async\(\)=>", js) is not None)
check("导航离开会确认", "leaveQuizGuard" in js and "async function navGo" in js)
check("弹窗开着时不抢键盘", "if(cfOpen())return;" in js)

print("\n  -- 做题总结 --")
check("总结含「需要加强的方面」", "需要加强的方面" in src)
check("总结含「掌握较好的方面」", "掌握较好的方面" in src)
check("中途结束也记历史（finished:!partial）", "finished: !partial" in js)
check("抽题带 topic 透传", re.search(r"topic:String\(x\.topic\|\|''\)\.trim\(\)", js) is not None)

print("\n  -- 修掉复习模式跳过题的 bug --")
check("本轮题目用快照 quizSet", "let quizSet=[]" in js)
check("getQuizSet 返回快照", re.search(r"function getQuizSet\(\)\{return quizSet;\}", js) is not None)
check("startQuiz 建立快照", re.search(r"quizSet=quizList\.slice\(\);", js) is not None)

print("\n  -- 第一步灰字已删 --")
check("已删除「后台异步处理」说明", "后台异步处理" not in src)
check("「不卡界面」也已删除", "不卡界面" not in src)

# ---- 6. 不泄露 Key ----
# 注意：页面里现在合法地会出现 `Bearer <登录token>`（历史记录接口要用）。
# 所以不能简单断言「没有 Bearer」，而要断言 Bearer 只拼登录 token，且页面里没有任何 sk- 密钥。
check("页面不含 SiliconFlow Key", "sk-nmzgm" not in src)
check("页面没有任何 sk- 开头的密钥", not re.search(r"sk-[A-Za-z0-9\-]{8,}", src))
bearer_ctx = re.findall(r".{0,14}Bearer.{0,14}", js)
check("Bearer 仅用于登录 token（不拼模型 Key）",
      bool(bearer_ctx) and all(("+t" in c or "${t}" in c) for c in bearer_ctx),
      bearer_ctx)
check("页面不直连 chat/completions", "/chat/completions" not in src)

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过 ✅")
