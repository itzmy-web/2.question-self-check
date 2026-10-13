# -*- coding: utf-8 -*-
"""历史记录的 SQLite 生产路径测试。

前面的 test_history.py 跑的是 USE_SQLITE=0（内存）路径。
**线上跑的是 SQLite**，两条分支必须都验；这个文件专测 SQL 分支。

做法：把 db.DB_PATH 指向临时文件，跑完即弃，不碰真实的 backend/data.db。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["USE_SQLITE"] = "1"        # 走 SQLite

import backend.db as db  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <-- {extra}"))


tmpdir = Path(tempfile.mkdtemp(prefix="scheck-db-"))
db.DB_PATH = tmpdir / "test.db"
print(f"USE_DB = {db.USE_DB} | 临时库 = {db.DB_PATH}\n")
check("USE_DB 为 True（走 SQLite 分支）", db.USE_DB is True)

db.init_db()

print("[1] 建表是否齐全")
import sqlite3  # noqa: E402
conn = sqlite3.connect(str(db.DB_PATH))
tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
for t in ("users", "tokens", "phone_codes", "qr_sessions",
          "history_docs", "history_sessions"):
    check(f"表 {t} 已创建", t in tables, sorted(tables))
idx = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
for i in ("ix_hdoc_user", "ix_hses_user"):
    check(f"索引 {i} 已创建", i in idx, sorted(idx))
conn.close()

print("\n[2] 老的登录功能未被破坏（回归）")
u = db.create_user("13800000000", "phone")
check("create_user 落库", bool(db.get_user_by_id(u["id"])))
db.save_token("tok-1", u["id"])
check("token 可换用户", (db.get_user_by_token("tok-1") or {}).get("id") == u["id"])
db.save_phone_code("13800000000", "654321", 9999999999.0)
check("验证码读回一致", (db.get_phone_code("13800000000") or {}).get("code") == "654321")
db.save_qr("tk1", "wx", 9999999999.0)
db.confirm_qr("tk1", u, "tok-2")
check("扫码会话 confirmed", (db.get_qr("tk1") or {}).get("status") == "confirmed")

print("\n[3] history_docs（SQL 分支）")
uid = u["id"]
qa = '[{"question":"光合作用的场所是？","answer":"叶绿体","topic":"光合作用"}]'
rid = db.add_history_doc(uid, "生物笔记.pdf", "pdf", "光合作用在叶绿体中进行。", qa)
check("add_history_doc 返回 id", bool(rid))
doc = db.get_history_doc(uid, rid)
check("get_history_doc 拿到正文", doc and "叶绿体" in doc["text"], doc)
check("qa 原样存回", doc and doc["qa"] == qa, doc and doc["qa"])
check("chars 已计算", doc and doc["chars"] == len("光合作用在叶绿体中进行。"), doc and doc["chars"])

lst = db.list_history_docs(uid)
check("list 返回 1 条", len(lst) == 1, lst)
check("list 计算了 qa_count=1", lst[0]["qa_count"] == 1, lst[0])
check("list 不含正文（列裁剪生效）", "text" not in lst[0], list(lst[0].keys()))

print("\n[4] history_sessions（SQL 分支）")
import json  # noqa: E402
sid = db.add_session(uid, {
    "doc_label": "生物笔记.pdf", "total": 4, "answered": 4, "ok": 3, "bad": 1,
    "choice_ok": 2, "choice_bad": 1, "score": 30, "max_score": 40,
    "finished": True,
    "aspects": json.dumps([{"topic": "光合作用", "total": 2, "ok": 2, "bad": 0, "rate": 100}]),
    "detail": json.dumps([{"question": "x", "topic": "y", "result": "bad"}]),
})
check("add_session 返回 id", bool(sid))
s = db.get_session(uid, sid)
check("get_session 拿到记录", bool(s))
check("finished 存成 1", s and s["finished"] == 1, s and s["finished"])
check("score 存成数值", s and float(s["score"]) == 30.0, s and s["score"])
check("aspects JSON 可解析", s and json.loads(s["aspects"])[0]["topic"] == "光合作用")
lsts = db.list_sessions(uid)
check("session 列表 1 条", len(lsts) == 1, lsts)
check("session 列表不含明细", "aspects" not in lsts[0], list(lsts[0].keys()))

print("\n[5] 中途结束（finished=False -> 存 0）")
sid2 = db.add_session(uid, {"doc_label": "半途", "total": 10, "answered": 3,
                            "ok": 2, "bad": 1, "score": 20, "max_score": 100,
                            "finished": False})
check("finished 存成 0", db.get_session(uid, sid2)["finished"] == 0)
check("session 列表 2 条", len(db.list_sessions(uid)) == 2)

print("\n[6] 用户隔离（SQL 层）")
u2 = db.create_user("13900000000", "phone")
uid2 = u2["id"]
check("别人取不到我的文档", db.get_history_doc(uid2, rid) is None)
check("别人取不到我的总结", db.get_session(uid2, sid) is None)
check("别人列表为空", db.list_history_docs(uid2) == [] and db.list_sessions(uid2) == [])
check("别人删不掉我的文档", db.delete_history_doc(uid2, rid) is False)
check("我的文档还在", db.get_history_doc(uid, rid) is not None)

print("\n[7] 排序 / 删除 / 清空")
import time as _t  # noqa: E402
r_older = db.add_history_doc(uid, "第一个", "text", "aaa", "")
_t.sleep(0.02)
r_newer = db.add_history_doc(uid, "第二个", "text", "bbb", "")
names = [x["filename"] for x in db.list_history_docs(uid)]
check("列表按时间倒序（最新在前）", names[0] == "第二个", names)
check("限额生效", len(db.list_history_docs(uid, limit=1)) == 1)
check("删除自己的文档", db.delete_history_doc(uid, r_older) is True)
check("重复删除返回 False", db.delete_history_doc(uid, r_older) is False)
removed = db.clear_history(uid)
check("清空返回移除数量", removed["docs"] == 2 and removed["sessions"] == 2, removed)
check("清空后 docs 为空", db.list_history_docs(uid) == [])
check("清空后 sessions 为空", db.list_sessions(uid) == [])
check("清空不影响别人（他人本来就空）", db.list_history_docs(uid2) == [])

print("\n[8] 持久化：重开连接仍在（真落盘，不是内存）")
db.add_history_doc(uid, "落盘验证", "text", "zzz", "")
check("重开 sqlite 连接读得到",
      len(sqlite3.connect(str(db.DB_PATH)).execute(
          "SELECT id FROM history_docs").fetchall()) == 1)

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过 ✅")
