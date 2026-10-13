# -*- coding: utf-8 -*-
"""访问码门槛回归测试（不联网、不消耗模型额度）。

覆盖：
  1. /api/llm-config 仍公开，且回传 access_required=True，且不泄露 Key
  2. /api/access/verify：空码 -> 401；错码 -> 401；对码 -> 200
  3. 受保护接口（/api/doc/upload、/api/job/x、/api/extract、/api/options）
     无码 -> 401，错码 -> 401
  4. 带对码时能穿过门槛（upload 返回 job_id，证明放行）
  5. 登录/注册等公开接口不受影响
  6. 静态页面（/index.html）不受门槛影响
  7. 失败次数超限 -> 429
  8. ACCESS_CODE 显式设为空字符串 -> 门槛关闭（显式逃生门）
  9. 未设 ACCESS_CODE（默认）-> 门槛关闭（B4：自部署场景默认不设门槛，需要时环境变量开启）

注意：访问码的**默认值是关闭**（B4，2026-10-13：应用户要求改为默认关闭；
用户将自部署到自有服务器）。测试「指定码」行为时显式设置 ACCESS_CODE 即可。
"""
import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("USE_SQLITE", "0")
TEST_CODE = "test-code-2026"
os.environ["ACCESS_CODE"] = TEST_CODE        # 显式开启，用于测试「启用门槛」的行为

from fastapi.testclient import TestClient  # noqa: E402

import backend.main as m  # noqa: E402

CODE = m.ACCESS_CODE
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <-- {extra}"))


client = TestClient(m.app)
print(f"当前访问码 = {CODE!r}\n")
check("测试码已被读取（门槛处于启用态）", CODE == TEST_CODE, CODE)

print("[1] /api/llm-config 公开 + 不泄露 Key")
r = client.get("/api/llm-config")
j = r.json()
check("llm-config 200", r.status_code == 200, r.status_code)
check("access_required=True", j.get("access_required") is True, j)
# A3/B3：server_key 应与「Key 实际可用性」一致——环境变量 LLM_API_KEY 或
# backend/llm_key.txt 任一存在即为 True，两者皆无为 False（B3 密钥文件兜底）
from pathlib import Path  # noqa: E402
_key_file = Path(ROOT) / "backend" / "llm_key.txt"
_key_expected = bool(os.getenv("LLM_API_KEY")) or _key_file.exists()
check("server_key=%s（环境变量/密钥文件：%s）" % (_key_expected, "有" if _key_expected else "无"),
      j.get("server_key") is _key_expected, j)
check("响应体不含 Key", "sk-nmzgm" not in r.text, r.text[:200])
check("响应体不含访问码", CODE not in r.text, r.text[:200])

print("\n[2] /api/access/verify")
r = client.post("/api/access/verify", json={"code": ""})
check("空码 -> 401", r.status_code == 401, r.status_code)
r = client.post("/api/access/verify", json={"code": "wrong-code"})
check("错码 -> 401", r.status_code == 401, r.status_code)
r = client.post("/api/access/verify", json={"code": "  "})
check("空白码 -> 401", r.status_code == 401, r.status_code)
r = client.post("/api/access/verify", json={"code": CODE})
check("对码 -> 200", r.status_code == 200, r.status_code)
check("对码 ok=True", r.json().get("ok") is True, r.json())
r = client.post("/api/access/verify", json={"code": " " + CODE + " "})
check("对码带空格也能过（strip）", r.status_code == 200, r.status_code)

print("\n[3] 受保护接口：无码 / 错码")
BAD = {"X-Access-Code": "nope"}
r = client.post("/api/doc/upload",
                files={"file": ("t.txt", b"1. hello", "text/plain")}, data={"kind": "text"})
check("upload 无码 -> 401", r.status_code == 401, r.status_code)
r = client.post("/api/doc/upload", headers=BAD,
                files={"file": ("t.txt", b"1. hello", "text/plain")}, data={"kind": "text"})
check("upload 错码 -> 401", r.status_code == 401, r.status_code)
check("job 无码 -> 401", client.get("/api/job/abc").status_code == 401)
check("extract 无码 -> 401", client.post("/api/extract", json={"text": "x"}).status_code == 401)
check("extract 错码 -> 401",
      client.post("/api/extract", json={"text": "x"}, headers=BAD).status_code == 401)
check("options 无码 -> 401", client.post("/api/options", json={"items": []}).status_code == 401)
check("options 错码 -> 401",
      client.post("/api/options", json={"items": []}, headers=BAD).status_code == 401)

print("\n[4] 带对码：成功穿过门槛")
H = {"X-Access-Code": CODE}
r = client.post("/api/doc/upload", headers=H,
                files={"file": ("t.txt", b"1. hello world", "text/plain")}, data={"kind": "text"})
check("upload 对码 -> 200", r.status_code == 200, r.text[:200])
check("upload 返回 job_id", bool(r.json().get("job_id")), r.text[:200])
jid = r.json().get("job_id") if r.status_code == 200 else None
if jid:
    rr = client.get(f"/api/job/{jid}", headers=H)
    check("job 对码 -> 200", rr.status_code == 200, rr.status_code)
    import time
    for _ in range(40):
        d = client.get(f"/api/job/{jid}", headers=H).json()
        if d.get("status") != "processing":
            break
        time.sleep(0.1)
    check("任务最终 done", d.get("status") == "done", d)
    check("文本内容正确", "hello world" in (d.get("text") or ""), d.get("text"))

print("\n[5] 公开接口不受门槛影响")
check("auth/config 200", client.get("/api/auth/config").status_code == 200)
r = client.post("/api/auth/send-code", json={"phone": "13800000000"})
check("send-code 200", r.status_code == 200, r.status_code)
code = r.json().get("demo_code")
r = client.post("/api/auth/login", json={"phone": "13800000000", "code": code or "000000"})
check("login 200", r.status_code == 200, r.status_code)

print("\n[6] 静态资源不受门槛影响")
check("index.html 200", client.get("/index.html").status_code == 200)
check("login.html 200", client.get("/login.html").status_code == 200)
check("根路径 307 跳登录", client.get("/", follow_redirects=False).status_code in (307, 302))

print("\n[7] 暴力尝试 -> 429 锁定")
m._ACCESS_FAILS.clear()
got429 = False
for i in range(m.ACCESS_MAX_FAILS + 3):
    rr = client.post("/api/access/verify", json={"code": f"bad{i}"})
    if rr.status_code == 429:
        got429 = True
        break
check("连续错码最终 429", got429, f"试了 {i+1} 次仍未 429")
try:
    m._ACCESS_FAILS.clear()
except Exception:
    pass

print("\n[8] 关闭门槛（ACCESS_CODE 显式设为空字符串）")
os.environ["ACCESS_CODE"] = ""
importlib.reload(m)
m._ACCESS_FAILS.clear()
c2 = TestClient(m.app)
check("空 ACCESS_CODE：ACCESS_CODE 为空", m.ACCESS_CODE == "", m.ACCESS_CODE)
check("空 ACCESS_CODE：llm-config 报 access_required=False",
      c2.get("/api/llm-config").json().get("access_required") is False)
check("空 ACCESS_CODE：verify 放行",
      c2.post("/api/access/verify", json={"code": ""}).status_code == 200)
check("空 ACCESS_CODE：extract 放行（无码不再是 401）",
      c2.post("/api/extract", json={"text": ""}).status_code == 400)  # 400=文本为空，说明已过门槛
check("空 ACCESS_CODE：upload 放行（无码不再是 401）",
      c2.post("/api/doc/upload", files={"file": ("t.txt", b"hi", "text/plain")},
              data={"kind": "text"}).status_code == 200)
check("空 ACCESS_CODE：job 无码不再 401（404=任务不存在，说明已过门槛）",
      c2.get("/api/job/notexist").status_code == 404)

print("\n[9] 默认（未设 ACCESS_CODE）应为关闭 —— B4（2026-10-13 用户指令：自部署服务器，默认不设门槛）")
os.environ.pop("ACCESS_CODE", None)
importlib.reload(m)
m._ACCESS_FAILS.clear()
c3 = TestClient(m.app)
check("未设 ACCESS_CODE -> ACCESS_CODE 为空（默认关闭）", m.ACCESS_CODE == "", m.ACCESS_CODE)
check("未设 ACCESS_CODE -> access_required=False",
      c3.get("/api/llm-config").json().get("access_required") is False)
check("未设 ACCESS_CODE -> extract 无码放行（400=文本为空，说明已过门槛）",
      c3.post("/api/extract", json={"text": ""}).status_code == 400)
check("未设 ACCESS_CODE -> upload 无码放行",
      c3.post("/api/doc/upload", files={"file": ("t.txt", b"hi", "text/plain")},
              data={"kind": "text"}).status_code == 200)
check("未设 ACCESS_CODE -> verify 返回 required=False",
      c3.post("/api/access/verify", json={"code": "whatever"}).json().get("required") is False)

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过 ✅")
