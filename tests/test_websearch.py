# -*- coding: utf-8 -*-
"""智能搜索端到端：真实服务器 + 真实百度搜索 + 真实 AI 总结。"""
import json
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8126"
code = (
    "import sys, os\n"
    "sys.path.insert(0, r'C:/Users/zmy/WorkBuddy/2026-09-28-15-29-59/self-check-quiz')\n"
    "os.chdir(r'C:/Users/zmy/WorkBuddy/2026-09-28-15-29-59/self-check-quiz')\n"
    "import uvicorn\n"
    "uvicorn.run('backend.main:app', host='127.0.0.1', port=8126, log_level='warning')\n"
)
proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")

def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=120)
    return json.loads(r.read().decode())

try:
    for _ in range(40):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(BASE + "/login.html", timeout=2)
            break
        except Exception:
            if proc.poll() is not None:
                print("SERVER DIED:\n", proc.stdout.read()[-1200:]); raise SystemExit(1)
    print("server up")

    d = post("/api/websearch", {"q": "什么是树莓派", "summarize": True})
    print("ok:", d.get("ok"), "| results:", len(d.get("results", [])), "| summary len:", len(d.get("summary", "")))
    for r in d.get("results", [])[:3]:
        print("  [%s] %s -> %s" % (r["url"][:40], r["title"][:30], (r["snippet"] or "")[:40]))
    print("AI 总结:", (d.get("summary") or "(无)")[:150])
    assert d.get("ok") and len(d.get("results", [])) >= 3, "搜索结果太少"
    assert d.get("summary"), "AI 总结为空"
    print("WEBSEARCH: PASS")
finally:
    proc.terminate()
