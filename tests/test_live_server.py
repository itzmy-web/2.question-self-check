# -*- coding: utf-8 -*-
"""真实 HTTP 端到端测试。

用「临时副本 + 全新数据库」起一个真的 uvicorn（走 serve.py，和线上一致），
再用真 HTTP 请求跑完整链路：
    登录 -> 上传文档(异步任务) -> 轮询 -> 保存历史 -> 查历史 -> 用户隔离 -> 静态页
跑完自动关服务、清理临时目录。

不碰项目的 backend/data.db，也不联网调用真实大模型。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8944
BASE = f"http://127.0.0.1:{PORT}"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <-- {extra}"))


def req(path, method="GET", data=None, headers=None, raw=None, ctype="application/json"):
    url = BASE + path
    h = dict(headers or {})
    body = None
    if raw is not None:
        body = raw
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        h.setdefault("Content-Type", ctype)
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jreq(*a, **k):
    code, body = req(*a, **k)
    try:
        return code, json.loads(body.decode("utf-8"))
    except Exception:
        return code, {"_raw": body[:200].decode("utf-8", "ignore")}


# ---------- 准备临时副本（不含 data.db，确保是干净库） ----------
tmp = tempfile.mkdtemp(prefix="scheck-e2e-")
for item in ("backend", "index.html", "login.html", "serve.py", "requirements.txt"):
    s = os.path.join(ROOT, item)
    d = os.path.join(tmp, item)
    if os.path.isdir(s):
        shutil.copytree(s, d)
    else:
        shutil.copy2(s, d)
for junk in ("data.db", "data.db-wal", "data.db-shm"):
    p = os.path.join(tmp, "backend", junk)
    if os.path.exists(p):
        os.remove(p)
print(f"临时副本：{tmp}\n（已移除 data.db，保证全新库）\n")

env = dict(os.environ)
env["PORT"] = str(PORT)
env["ACCESS_CODE"] = ""               # 显式关闭门槛（A4 后默认开启，测试用逃生门关闭）
env["USE_SQLITE"] = "1"               # 走 SQLite，和线上一致

proc = subprocess.Popen(
    [sys.executable, "serve.py"], cwd=tmp, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
try:
    # 等服务就绪
    ok = False
    for _ in range(60):
        try:
            code, _b = req("/api/llm-config")
            if code == 200:
                ok = True
                break
        except Exception:
            pass
        time.sleep(0.4)
    check("serve.py 成功启动并监听 PORT", ok)
    if not ok:
        raise SystemExit(1)

    print("\n[1] 服务健康")
    code, d = jreq("/api/llm-config")
    check("llm-config 200", code == 200, code)
    check("access_required=False（门槛已关）", d.get("access_required") is False, d)

    print("\n[2] 登录（走真 HTTP）")
    code, d = jreq("/api/auth/send-code", "POST", {"phone": "13700000001"})
    check("发码 200", code == 200, (code, d))
    code_login = d.get("demo_code")
    code, d = jreq("/api/auth/login", "POST", {"phone": "13700000001", "code": code_login})
    check("登录 200", code == 200, (code, d))
    tokA = d.get("token")
    HA = {"Authorization": "Bearer " + tokA}

    code, d = jreq("/api/auth/send-code", "POST", {"phone": "13700000002"})
    code, d = jreq("/api/auth/login", "POST", {"phone": "13700000002", "code": d.get("demo_code")})
    HB = {"Authorization": "Bearer " + d.get("token")}

    print("\n[3] 上传文档 -> 异步任务 -> 轮询（真 multipart）")
    boundary = "----scheckE2E"
    content = "1. 光合作用的场所是？\n答：叶绿体\n2. 细胞膜的主要成分？\n答：磷脂和蛋白质\n".encode("utf-8")
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"bio.txt\"\r\n"
                 f"Content-Type: text/plain\r\n\r\n".encode() + content + b"\r\n")
    for k, v in (("kind", "text"), ("apiKey", ""), ("baseUrl", ""), ("ocrModel", "")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    code, d = jreq("/api/doc/upload", "POST", raw=body,
                   headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    check("上传 200", code == 200, (code, d))
    jid = d.get("job_id")
    check("拿到 job_id", bool(jid))
    status = None
    for _ in range(40):
        code, d = jreq(f"/api/job/{jid}")
        status = d.get("status")
        if status in ("done", "error"):
            break
        time.sleep(0.25)
    check("任务完成", status == "done", d)
    check("解析出正文", "光合作用" in (d.get("text") or ""), d.get("text"))

    print("\n[4] 保存历史（文档 + 答案）")
    doc = {
        "filename": "生物笔记.txt", "kind": "text",
        "text": d.get("text") or "",
        "questions": [
            {"question": "光合作用的场所是？", "answer": "叶绿体", "topic": "光合作用", "options": []},
            {"question": "细胞膜的主要成分？", "answer": "磷脂和蛋白质", "topic": "细胞结构", "options": []},
        ],
    }
    code, d = jreq("/api/history/doc", "POST", doc, HA)
    check("保存文档 200", code == 200, (code, d))
    doc_id = d.get("id")

    code, d = jreq("/api/history/docs", headers=HA)
    items = d.get("items") or []
    check("列表 1 条", len(items) == 1, items)
    check("qa_count=2", items and items[0].get("qa_count") == 2, items)

    code, d = jreq(f"/api/history/doc/{doc_id}", headers=HA)
    check("取单条含 questions", len((d.get("item") or {}).get("questions") or []) == 2, d)
    check("questions 带 topic",
          (d["item"]["questions"][0].get("topic") == "光合作用"), d["item"]["questions"][0])

    print("\n[5] 保存做题总结（含方面分析）")
    ses = {
        "doc_label": "生物笔记.txt", "total": 2, "answered": 2, "ok": 1, "bad": 1,
        "choice_ok": 1, "choice_bad": 0, "score": 10, "max_score": 20, "finished": True,
        "aspects": [{"topic": "光合作用", "total": 1, "ok": 1, "bad": 0, "rate": 100},
                    {"topic": "细胞结构", "total": 1, "ok": 0, "bad": 1, "rate": 0}],
        "detail": [{"question": "细胞膜的主要成分？", "topic": "细胞结构", "result": "bad"}],
    }
    code, d = jreq("/api/history/session", "POST", ses, HA)
    check("保存总结 200", code == 200, (code, d))
    ses_id = d.get("id")
    code, d = jreq(f"/api/history/session/{ses_id}", headers=HA)
    it = d.get("item") or {}
    check("总结含 2 个方面", len(it.get("aspects") or []) == 2, it)
    check("总结含 detail", len(it.get("detail") or []) == 1, it)

    print("\n[6] 用户隔离（真 HTTP）")
    code, d = jreq("/api/history/docs", headers=HB)
    check("B 看不到 A 的文档", (d.get("items") or []) == [], d)
    check("B 取 A 的文档 404", jreq(f"/api/history/doc/{doc_id}", headers=HB)[0] == 404)
    check("B 删 A 的文档不生效",
          jreq(f"/api/history/doc/{doc_id}", "DELETE", headers=HB)[1].get("ok") is False)
    code, d = jreq(f"/api/history/doc/{doc_id}", headers=HA)
    check("A 的文档还在", code == 200, code)
    check("无 token 401", jreq("/api/history/docs")[0] == 401)

    print("\n[7] 静态页与本次改动")
    code, body = req("/index.html")
    html = body.decode("utf-8", "ignore")
    check("index.html 200", code == 200, code)
    check("已含任务栏", ">任务栏<" in html)
    check("已含历史记录入口", 'id="stepH"' in html)
    check("已含确认弹窗", 'id="confirmModal"' in html)
    check("已删除灰字说明", "后台异步处理" not in html)
    check("页面不含模型 Key", "sk-nmzgm" not in html)
    check("login.html 200", req("/login.html")[0] == 200)

    print("\n[8] SQL 落盘（重启后数据仍在）")
    proc.terminate()
    proc.wait(timeout=15)
    proc = subprocess.Popen([sys.executable, "serve.py"], cwd=tmp, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ready = False
    for _ in range(60):
        try:
            if req("/api/llm-config")[0] == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.4)
    check("重启后服务可用", ready)
    code, d = jreq("/api/history/docs", headers=HA)
    check("重启后历史还在（真落盘）", len(d.get("items") or []) == 1, d)

finally:
    try:
        proc.terminate()
        proc.wait(timeout=15)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n（已关闭服务并清理 {tmp}）")

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过 ✅")
