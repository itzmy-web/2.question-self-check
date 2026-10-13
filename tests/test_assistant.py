# -*- coding: utf-8 -*-
"""AI 客服端到端测试：起真实服务器，调 /api/assistant/chat 两次（项目问题 + 无关话题兜底）。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8125"
code = (
    "import sys, os\n"
    "sys.path.insert(0, r'C:/Users/zmy/WorkBuddy/2026-09-28-15-29-59/self-check-quiz')\n"
    "os.chdir(r'C:/Users/zmy/WorkBuddy/2026-09-28-15-29-59/self-check-quiz')\n"
    "import uvicorn\n"
    "uvicorn.run('backend.main:app', host='127.0.0.1', port=8125, log_level='warning')\n"
)
import subprocess
proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")

def chat(msg, history=None):
    body = json.dumps({"message": msg, "history": history or []}).encode()
    req = urllib.request.Request(BASE + "/api/assistant/chat", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=90)
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

    q1 = "这个项目怎么用？一句话告诉我"
    d1 = chat(q1)
    print("[项目问题]", d1.get("ok"), "->", (d1.get("reply") or "")[:120].replace("\n", " "))
    assert d1.get("ok") and len(d1.get("reply", "")) > 5

    q2 = "今天北京天气怎么样？"
    d2 = chat(q2)
    print("[无关兜底]", d2.get("ok"), "->", (d2.get("reply") or "")[:120].replace("\n", " "))
    assert d2.get("ok") and len(d2.get("reply", "")) > 5

    # 带历史的多轮
    d3 = chat("那第二步是什么？", [{"role": "user", "content": q1}, {"role": "assistant", "content": d1["reply"]}])
    print("[多轮追问]", d3.get("ok"), "->", (d3.get("reply") or "")[:120].replace("\n", " "))
    assert d3.get("ok") and len(d3.get("reply", "")) > 5

    print("ASSISTANT: PASS")
finally:
    proc.terminate()
