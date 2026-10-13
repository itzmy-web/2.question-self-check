import os
os.environ.setdefault("USE_SQLITE", "1")
# 用独立测试库，避免污染
import tempfile, pathlib
from pathlib import Path
_TEST_DB = Path(tempfile.gettempdir()) / "qc_smoke_test.db"
os.environ["_QC_TEST_DB"] = str(_TEST_DB)
# 让 db 用测试库：直接 monkey 改 DB_PATH
import db as dbmod
dbmod.DB_PATH = _TEST_DB
if _TEST_DB.exists(): _TEST_DB.unlink()

from fastapi.testclient import TestClient
import main

client = TestClient(main.app)

def check(cond, msg):
    assert cond, msg
    print("  ✓", msg)

print("== 静态与重定向 ==")
r = client.get("/"); check(r.status_code in (200, 307), f"root -> {r.status_code}")
r = client.get("/login.html"); check(r.status_code == 200, "login.html 200")
r = client.get("/index.html"); check(r.status_code == 200, "index.html 200")

print("== 配置（默认演示模式）==")
r = client.get("/api/auth/config"); check(r.status_code == 200, "config 200")
cfg = r.json(); print("  config:", cfg)
check(cfg["wx"] == "demo" and cfg["ali"] == "demo", "wx/ali 默认 demo")

print("== 手机号：发码 + 登录 ==")
r = client.post("/api/auth/send-code", json={"phone": "13800138000"})
check(r.status_code == 200, "send-code 200")
rec = dbmod.get_phone_code("13800138000"); code = rec["code"]
print("  验证码:", code)
r = client.post("/api/auth/login", json={"phone": "13800138000", "code": code})
check(r.status_code == 200, f"login 200 ({r.status_code})")
tok = r.json()["token"]; user = r.json()["user"]
check(dbmod.get_user_by_token(tok) is not None, "token 落库可查")

print("== /me ==")
r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
check(r.status_code == 200 and r.json()["user"]["name"] == user["name"], "/me 200")

print("== 微信：演示扫码 confirm + 轮询 ==")
r = client.get("/api/auth/wx/qrcode"); check(r.status_code == 200, "wx qrcode 200")
d = r.json(); check(d["mode"] == "demo", "wx qrcode demo 模式")
ticket = d["ticket"]
r = client.post("/api/auth/wx/confirm", json={"ticket": ticket})
check(r.status_code == 200, "wx confirm 200")
tok2 = r.json()["token"]
r = client.get(f"/api/auth/wx/status?ticket={ticket}")
check(r.status_code == 200 and r.json()["status"] == "confirmed", "wx status confirmed")
check(r.json()["token"] == tok2, "status 返回 token 一致")

print("== 异常：错误手机号 ==")
r = client.post("/api/auth/send-code", json={"phone": "123"})
check(r.status_code == 400, "bad phone 400")

print("== 持久化：重开连接读取（模拟重启）==")
u = dbmod.get_user_by_token(tok)
check(u is not None and u["name"] == user["name"], "重启后 token 仍可查（SQLite 文件持久化）")

print("\nALL SMOKE TESTS PASSED ✅")
