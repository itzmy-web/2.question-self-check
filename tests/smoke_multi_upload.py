# -*- coding: utf-8 -*-
"""并发上传冒烟测试：3 个文件同时传（含 25MB 大文件），并行轮询到全部完成。"""
import concurrent.futures as cf
import io
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8124"

# 启动服务器（独立进程）
code = (
    "import sys, os\n"
    "sys.path.insert(0, r'C:/Users/zmy/WorkBuddy/2026-09-28-15-29-59/self-check-quiz')\n"
    "os.chdir(r'C:/Users/zmy/WorkBuddy/2026-09-28-15-29-59/self-check-quiz')\n"
    "import uvicorn\n"
    "uvicorn.run('backend.main:app', host='127.0.0.1', port=8124, log_level='warning')\n"
)
proc = __import__("subprocess").Popen(
    [sys.executable, "-c", code],
    stdout=__import__("subprocess").PIPE,
    stderr=__import__("subprocess").STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
)

def wait_up():
    for _ in range(40):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(BASE + "/login.html", timeout=2)
            return True
        except Exception:
            if proc.poll() is not None:
                print("SERVER DIED:\n", proc.stdout.read()[-1500:])
                return False
    return False

def multipart(fields, files):
    bound = "----bnd1234567890"
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (bound, k, v)).encode())
    for k, (fn, data) in files.items():
        body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n" % (bound, k, fn)).encode())
        body.write(data)
        body.write(b"\r\n")
    body.write(("--%s--\r\n" % bound).encode())
    return body.getvalue(), bound

def upload(name, data):
    body, bound = multipart({"kind": "text"}, {"file": (name, data)})
    req = urllib.request.Request(BASE + "/api/doc/upload", data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + bound})
    r = urllib.request.urlopen(req, timeout=60)
    return json.loads(r.read().decode())

def poll(jid):
    t0 = time.time()
    while time.time() - t0 < 60:
        r = urllib.request.urlopen(BASE + "/api/job/" + jid, timeout=10)
        d = json.loads(r.read().decode())
        if d["status"] in ("done", "error"):
            return d
        time.sleep(0.4)
    return {"status": "timeout"}

try:
    if not wait_up():
        print("SMOKE: FAIL (server not up)")
        raise SystemExit(1)
    print("server up")

    big = ("大文件测试内容第%d行，用于撑过旧版24MB上限。\n" % 0) * 1
    big_text = (" Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor.\n" * 320000)  # ~25.9MB
    files = [
        ("a_small.txt", "小文件A的内容：光合作用发生在叶绿体。".encode()),
        ("b_big.txt", big_text.encode()),
        ("c_small.txt", "小文件C的内容：线粒体是动力车间。".encode()),
    ]
    print("sizes:", [(n, len(d)) for n, d in files])

    t0 = time.time()
    with cf.ThreadPoolExecutor(3) as ex:
        futs = [ex.submit(upload, n, d) for n, d in files]
        results = [f.result() for f in futs]
    print("upload roundtrip: %.1fs" % (time.time() - t0))
    for r in results:
        print("  ->", r.get("ok"), r.get("job_id", "")[:8], r.get("kind"), r.get("filename"))

    with cf.ThreadPoolExecutor(3) as ex:
        futs = [ex.submit(poll, r["job_id"]) for r in results]
        done = [f.result() for f in futs]
    ok = True
    for (n, _), d in zip(files, done):
        st = d.get("status")
        tl = len(d.get("text") or "")
        print("  [%s] %s -> %d chars %s" % (st, n, tl, (d.get("error") or "")[:60]))
        if st != "done" or tl == 0:
            ok = False
    # 大文件确实超过旧 24MB 上限
    assert len(files[1][1]) > 24 * 1024 * 1024, "big file not big enough"
    print("big file size: %.1f MB (旧上限 24MB 已被接受)" % (len(files[1][1]) / 1048576))
    print("SMOKE:", "PASS" if ok else "FAIL")
finally:
    proc.terminate()
