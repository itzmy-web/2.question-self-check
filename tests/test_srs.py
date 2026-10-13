# -*- coding: utf-8 -*-
"""SRS 间隔重复最小版测试：喂卡 → 到期 → 调度 → 隔离（内存与 SQLite 双模式）。"""
import os, sys, tempfile, time

def run_mode(use_db: bool):
    os.environ["USE_SQLITE"] = "1" if use_db else "0"
    os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "srs.db")
    for k in list(sys.modules):
        if k.startswith("backend"):
            del sys.modules[k]
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from fastapi.testclient import TestClient
    import backend.main as m

    c = TestClient(m.app)
    ok = []
    def check(name, cond, extra=""):
        ok.append(cond)
        print(("  ✅ " if cond else "  ❌ ") + name + (f"  ({extra})" if extra else ""))

    print(f"== 模式: {'SQLite' if use_db else '内存'} ==")
    # 登录
    d = c.post("/api/auth/send-code", json={"phone": "13900001111"}).json()
    tok = c.post("/api/auth/login", json={"phone": "13900001111", "code": d["demo_code"]}).json()["token"]
    H = {"Authorization": "Bearer " + tok}

    # [1] 初始为空
    r = c.get("/api/srs/due", headers=H).json()
    check("初始 due=0", r["due"] == 0 and r["total"] == 0)

    # [2] 喂 3 题：2 对 1 错
    items = [
        {"question": "IPv4 地址长度是多少位？", "answer": "32 位", "qtype": "single",
         "options": ["32 位", "64 位", "128 位"], "correct": "A", "topic": "IPv4", "result": "ok"},
        {"question": "以太网传统 MTU 是多少字节？", "answer": "1500 字节", "qtype": "single",
         "options": ["1500 字节", "1000 字节"], "correct": "A", "topic": "MTU", "result": "ok"},
        {"question": "TCP 面向连接且提供可靠传输。", "answer": "正确", "qtype": "judge",
         "options": ["正确", "错误"], "correct": "A", "topic": "TCP", "result": "bad"},
    ]
    r = c.post("/api/srs/ingest", json={"items": items}, headers=H).json()
    check("喂卡 added=3", r["added"] == 3, str(r))

    # [3] 答对的卡间隔 1 天 → 不到期；答错的卡当天到期
    r = c.get("/api/srs/due", headers=H).json()
    check("due 只含答错的 1 张", r["due"] == 1 and r["cards"][0]["question"].startswith("TCP"),
          f"due={r['due']}")

    # [4] 同卡再喂一次 ok → streak 递增（updated 而非 added）
    r = c.post("/api/srs/ingest", json={"items": [items[2] | {"result": "ok"}]}, headers=H).json()
    check("错卡翻正 updated=1 added=0", r["updated"] == 1 and r["added"] == 0, str(r))
    r = c.get("/api/srs/due", headers=H).json()
    check("翻正后 due=0（1 天后再见）", r["due"] == 0)

    # [5] 再喂一次 bad → 回到当天到期
    c.post("/api/srs/ingest", json={"items": [items[2] | {"result": "bad"}]}, headers=H)
    r = c.get("/api/srs/due", headers=H).json()
    check("再错回到 due=1", r["due"] == 1)

    # [6] skip 结果不入库
    before = c.get("/api/srs/stats", headers=H).json()
    c.post("/api/srs/ingest", json={"items": [items[0] | {"result": "skip"}]}, headers=H)
    after = c.get("/api/srs/stats", headers=H).json()
    check("skip 不改变统计", before["total"] == after["total"] and before["due"] == after["due"])

    # [7] 用户隔离
    d2 = c.post("/api/auth/send-code", json={"phone": "13900002222"}).json()
    tok2 = c.post("/api/auth/login", json={"phone": "13900002222", "code": d2["demo_code"]}).json()["token"]
    r2 = c.get("/api/srs/due", headers={"Authorization": "Bearer " + tok2}).json()
    check("用户 B 看不到 A 的卡片", r2["total"] == 0)

    # [8] 未登录 401
    check("无 token 401", c.get("/api/srs/due").status_code == 401)

    n = sum(1 for x in ok if x)
    print(f"== {'SQLite' if use_db else '内存'} 模式: {n}/{len(ok)} 通过 ==")
    return all(ok)


allok = run_mode(False) and run_mode(True)
print("=" * 40)
print("SRS 全部通过 ✅" if allok else "SRS 有失败 ❌")
sys.exit(0 if allok else 1)
