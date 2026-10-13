# -*- coding: utf-8 -*-
"""
问题自检 · SQLite 持久化层

使用标准库 sqlite3，零额外依赖，适合本地运行与离线/国内部署。
所有访问都「按需开连接、用完即关」，配合 WAL + busy_timeout 兼容 uvicorn 多worker 并发。

两种存储模式：
  USE_SQLITE=1（默认）  写入 backend/data.db，进程重启数据仍在
  USE_SQLITE=0         纯内存演示模式，进程重启即清空（读写都走 _MEM，行为一致）

表结构：
  users       用户（provider + identifier 唯一）
  tokens      登录令牌
  phone_codes 手机号验证码
  qr_sessions 扫码登录会话（含 OAuth 模式下的 token）
"""
import os
import time
import json
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH") or (Path(__file__).resolve().parent / "data.db"))
# 设为 "0" 可强制关闭持久化（回退为纯内存演示）
USE_DB = os.getenv("USE_SQLITE", "1") != "0"

# ---------------- 内存回退（USE_SQLITE=0 时使用） ----------------
# 注意：read 和 write 必须用同一份存储，否则会出现「写进去了却读不到」。
# 仅用于演示 / 测试，多 worker 下不共享，进程重启即清空。
_MEM = {"users": {}, "tokens": {}, "phone_codes": {}, "qr": {}, "docs": {}, "sessions": {}, "srs": {}}


def _conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建表（幂等）。USE_DB=0 时跳过。"""
    if not USE_DB:
        return
    conn = _conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id         TEXT PRIMARY KEY,
            provider   TEXT NOT NULL,
            identifier TEXT,
            name       TEXT,
            avatar     TEXT,
            created_at REAL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS u_provider_identifier
            ON users(provider, identifier);

        CREATE TABLE IF NOT EXISTS tokens (
            token      TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            created_at REAL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS phone_codes (
            phone TEXT PRIMARY KEY,
            code  TEXT,
            exp   REAL
        );

        CREATE TABLE IF NOT EXISTS qr_sessions (
            ticket  TEXT PRIMARY KEY,
            provider TEXT,
            status  TEXT,
            user_id TEXT,
            token   TEXT,
            exp     REAL
        );

        -- 历史记录：用户上传的文档 + 抽取出的题目/答案
        CREATE TABLE IF NOT EXISTS history_docs (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            filename   TEXT,
            kind       TEXT,
            chars      INTEGER,
            qa_count   INTEGER,
            text       TEXT,
            qa         TEXT,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS ix_hdoc_user ON history_docs(user_id, created_at);

        -- 历史记录：每次做题的总结（刷完 或 中途结束）
        CREATE TABLE IF NOT EXISTS history_sessions (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            doc_label  TEXT,
            total      INTEGER,
            answered   INTEGER,
            ok         INTEGER,
            bad        INTEGER,
            choice_ok  INTEGER,
            choice_bad INTEGER,
            score      REAL,
            max_score  REAL,
            finished   INTEGER,
            aspects    TEXT,
            detail     TEXT,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS ix_hses_user ON history_sessions(user_id, created_at);

        -- SRS 间隔重复卡片（熟悉间隔翻倍，不熟悉当天重练）
        CREATE TABLE IF NOT EXISTS srs_cards (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            question   TEXT NOT NULL,
            answer     TEXT,
            qtype      TEXT,
            options    TEXT,
            correct    TEXT,
            topic      TEXT,
            streak     INTEGER DEFAULT 0,
            interval_d INTEGER DEFAULT 0,
            next_at    REAL,
            updated_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_srs_user ON srs_cards(user_id, next_at);
        """
    )
    conn.commit()
    conn.close()


def _user_from_row(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "provider": row["provider"],
        "identifier": row["identifier"],
        "name": row["name"],
        "avatar": row["avatar"],
    }


# ---------------- users ----------------
def get_user_by_provider(provider: str, identifier: str):
    if identifier is None:
        return None
    if not USE_DB:
        for u in _MEM["users"].values():
            if u["provider"] == provider and u["identifier"] == identifier:
                return dict(u)
        return None
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM users WHERE provider=? AND identifier=?",
        (provider, identifier),
    )
    row = cur.fetchone()
    conn.close()
    return _user_from_row(row)


def get_user_by_id(uid: str):
    if not USE_DB:
        u = _MEM["users"].get(uid)
        return dict(u) if u else None
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    return _user_from_row(row)


def create_user(identifier: str, provider: str, name: str = None, avatar: str = None):
    uid = secrets.token_hex(8)
    if name is None:
        name = (
            "微信用户"
            if provider == "wx"
            else "支付宝用户"
            if provider == "ali"
            else f"用户{(identifier or '')[-4:]}"
        )
    if not USE_DB:
        u = {"id": uid, "provider": provider, "identifier": identifier, "name": name, "avatar": avatar}
        _MEM["users"][uid] = u
        return dict(u)
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(id, provider, identifier, name, avatar, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (uid, provider, identifier, name, avatar, time.time()),
    )
    conn.commit()
    conn.close()
    return {"id": uid, "provider": provider, "identifier": identifier, "name": name, "avatar": avatar}


def update_user_name(uid: str, name: str, avatar: str = None):
    if not USE_DB:
        u = _MEM["users"].get(uid)
        if u:
            u["name"] = name
            if avatar is not None:
                u["avatar"] = avatar
        return
    conn = _conn()
    cur = conn.cursor()
    if avatar is None:
        cur.execute("UPDATE users SET name=? WHERE id=?", (name, uid))
    else:
        cur.execute("UPDATE users SET name=?, avatar=? WHERE id=?", (name, avatar, uid))
    conn.commit()
    conn.close()


# ---------------- tokens ----------------
def save_token(token: str, user_id: str):
    if not USE_DB:
        _MEM["tokens"][token] = user_id
        return
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO tokens(token, user_id, created_at) VALUES(?,?,?)",
        (token, user_id, time.time()),
    )
    conn.commit()
    conn.close()


def get_user_by_token(token: str):
    if not token:
        return None
    if not USE_DB:
        uid = _MEM["tokens"].get(token)
        u = _MEM["users"].get(uid) if uid else None
        return dict(u) if u else None
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?",
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    return _user_from_row(row)


# ---------------- phone_codes ----------------
def save_phone_code(phone: str, code: str, exp: float):
    if not USE_DB:
        _MEM["phone_codes"][phone] = {"phone": phone, "code": code, "exp": exp}
        return
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO phone_codes(phone, code, exp) VALUES(?,?,?)",
        (phone, code, exp),
    )
    conn.commit()
    conn.close()


def get_phone_code(phone: str):
    if not USE_DB:
        r = _MEM["phone_codes"].get(phone)
        return dict(r) if r else None
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM phone_codes WHERE phone=?", (phone,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------- qr_sessions ----------------
def save_qr(ticket: str, provider: str, exp: float):
    if not USE_DB:
        _MEM["qr"][ticket] = {
            "ticket": ticket, "provider": provider, "status": "pending",
            "user_id": None, "token": None, "exp": exp,
        }
        return
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO qr_sessions(ticket, provider, status, user_id, token, exp) "
        "VALUES(?,?,?,?,?,?)",
        (ticket, provider, "pending", None, None, exp),
    )
    conn.commit()
    conn.close()


def get_qr(ticket: str):
    if not USE_DB:
        r = _MEM["qr"].get(ticket)
        return dict(r) if r else None
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM qr_sessions WHERE ticket=?", (ticket,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def confirm_qr(ticket: str, user: dict, token: str):
    """将扫码会话标记为已确认，并绑定用户与令牌。"""
    if not USE_DB:
        r = _MEM["qr"].get(ticket)
        if r:
            r.update({"status": "confirmed", "user_id": user["id"], "token": token})
        return
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE qr_sessions SET status='confirmed', user_id=?, token=? WHERE ticket=?",
        (user["id"], token, ticket),
    )
    conn.commit()
    conn.close()


# ==================================================================
# 历史记录：用户上传的文档（含抽出的题目/答案）
#   所有读写都带 user_id 过滤，保证只能看到自己的记录。
# ==================================================================
def add_history_doc(user_id: str, filename: str, kind: str, text: str, qa: str) -> str:
    """存一条「上传的文档 + 抽取出的题目/答案」。qa 为 JSON 字符串。"""
    rid = secrets.token_hex(8)
    ts = time.time()
    n = len(text or "")
    try:
        qc = len(json.loads(qa)) if qa else 0
    except Exception:
        qc = 0
    if not USE_DB:
        _MEM["docs"][rid] = {
            "id": rid, "user_id": user_id, "filename": filename, "kind": kind,
            "chars": n, "qa_count": qc, "text": text, "qa": qa, "created_at": ts,
        }
        return rid
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO history_docs(id, user_id, filename, kind, chars, qa_count, text, qa, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (rid, user_id, filename, kind, n, qc, text, qa, ts),
    )
    conn.commit()
    conn.close()
    return rid


def list_history_docs(user_id: str, limit: int = 60):
    """列表：不返回正文字段（省流量），只给概要。"""
    if not USE_DB:
        rows = [v for v in _MEM["docs"].values() if v["user_id"] == user_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [
            {k: r[k] for k in ("id", "filename", "kind", "chars", "qa_count", "created_at")}
            for r in rows[:limit]
        ]
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, filename, kind, chars, qa_count, created_at FROM history_docs "
        "WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_history_doc(user_id: str, rid: str):
    """取单条完整记录（含正文与题目），用于「载入到工作区」。"""
    if not USE_DB:
        r = _MEM["docs"].get(rid)
        return dict(r) if r and r["user_id"] == user_id else None
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM history_docs WHERE id=? AND user_id=?", (rid, user_id))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_history_doc(user_id: str, rid: str) -> bool:
    if not USE_DB:
        r = _MEM["docs"].get(rid)
        if r and r["user_id"] == user_id:
            _MEM["docs"].pop(rid, None)
            return True
        return False
    conn = _conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM history_docs WHERE id=? AND user_id=?", (rid, user_id))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n > 0


# ==================================================================
# 历史记录：每次做题的总结（刷完 / 中途结束）
# ==================================================================
_SESSION_COLS = (
    "id", "user_id", "doc_label", "total", "answered", "ok", "bad",
    "choice_ok", "choice_bad", "score", "max_score", "finished",
    "aspects", "detail", "created_at",
)
_SESSION_BRIEF = (
    "id", "doc_label", "total", "answered", "ok", "bad",
    "choice_ok", "choice_bad", "score", "max_score", "finished", "created_at",
)


def add_session(user_id: str, data: dict) -> str:
    """存一次做题总结。data 见 main.py 的 SessionReq。"""
    sid = secrets.token_hex(8)
    rec = {
        "id": sid, "user_id": user_id,
        "doc_label": data.get("doc_label") or "",
        "total": int(data.get("total") or 0),
        "answered": int(data.get("answered") or 0),
        "ok": int(data.get("ok") or 0),
        "bad": int(data.get("bad") or 0),
        "choice_ok": int(data.get("choice_ok") or 0),
        "choice_bad": int(data.get("choice_bad") or 0),
        "score": float(data.get("score") or 0),
        "max_score": float(data.get("max_score") or 0),
        "finished": 1 if data.get("finished") else 0,
        "aspects": data.get("aspects") or "[]",
        "detail": data.get("detail") or "[]",
        "created_at": time.time(),
    }
    if not USE_DB:
        _MEM["sessions"][sid] = rec
        return sid
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO history_sessions({','.join(_SESSION_COLS)}) "
        f"VALUES({','.join('?' * len(_SESSION_COLS))})",
        tuple(rec[c] for c in _SESSION_COLS),
    )
    conn.commit()
    conn.close()
    return sid


def list_sessions(user_id: str, limit: int = 60):
    """列表：不带 aspects/detail 明细。"""
    if not USE_DB:
        rows = [v for v in _MEM["sessions"].values() if v["user_id"] == user_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [{k: r[k] for k in _SESSION_BRIEF} for r in rows[:limit]]
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT {','.join(_SESSION_BRIEF)} FROM history_sessions "
        "WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_session(user_id: str, sid: str):
    """取单次总结的完整明细（aspects + detail）。"""
    if not USE_DB:
        r = _MEM["sessions"].get(sid)
        return dict(r) if r and r["user_id"] == user_id else None
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM history_sessions WHERE id=? AND user_id=?", (sid, user_id))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(user_id: str, sid: str) -> bool:
    if not USE_DB:
        r = _MEM["sessions"].get(sid)
        if r and r["user_id"] == user_id:
            _MEM["sessions"].pop(sid, None)
            return True
        return False
    conn = _conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM history_sessions WHERE id=? AND user_id=?", (sid, user_id))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n > 0


def clear_history(user_id: str) -> dict:
    """清空该用户的全部历史。"""
    if not USE_DB:
        nd = len([1 for v in _MEM["docs"].values() if v["user_id"] == user_id])
        ns = len([1 for v in _MEM["sessions"].values() if v["user_id"] == user_id])
        for k in [k for k, v in _MEM["docs"].items() if v["user_id"] == user_id]:
            _MEM["docs"].pop(k, None)
        for k in [k for k, v in _MEM["sessions"].items() if v["user_id"] == user_id]:
            _MEM["sessions"].pop(k, None)
        return {"docs": nd, "sessions": ns}
    conn = _conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM history_docs WHERE user_id=?", (user_id,))
    nd = cur.rowcount
    cur.execute("DELETE FROM history_sessions WHERE user_id=?", (user_id,))
    ns = cur.rowcount
    conn.commit()
    conn.close()
    return {"docs": nd, "sessions": ns}

def migrate_user_data(from_uid: str, to_uid: str) -> dict:
    """游客数据迁移：把 from 用户（游客）的文档/做题历史并入 to 用户（正式账号）。"""
    if not USE_DB:
        nd = ns = 0
        for v in _MEM["docs"].values():
            if v["user_id"] == from_uid:
                v["user_id"] = to_uid; nd += 1
        for v in _MEM["sessions"].values():
            if v["user_id"] == from_uid:
                v["user_id"] = to_uid; ns += 1
        return {"docs": nd, "sessions": ns}
    conn = _conn()
    cur = conn.cursor()
    cur.execute("UPDATE history_docs SET user_id=? WHERE user_id=?", (to_uid, from_uid))
    nd = cur.rowcount
    cur.execute("UPDATE history_sessions SET user_id=? WHERE user_id=?", (to_uid, from_uid))
    ns = cur.rowcount
    conn.commit()
    conn.close()
    return {"docs": nd, "sessions": ns}

# ---------------- SRS 间隔重复（最小版：熟悉间隔翻倍，不熟悉当天重练） ----------------
_SRS_INTERVALS = [1, 3, 7, 14, 30]


def _srs_id(question: str) -> str:
    import hashlib
    return hashlib.md5((question or "").strip()[:200].encode("utf-8")).hexdigest()


def srs_ingest(user_id: str, items: list) -> dict:
    """喂卡 + 调度。item: {question,answer,qtype,options,correct,topic,result(ok/bad)}。skip 不入库。"""
    import json as _json
    now = time.time()
    added = updated = 0
    for it in items:
        q = (it.get("question") or "").strip()
        if not q:
            continue
        cid = _srs_id(q)
        result = it.get("result")
        ans = it.get("answer") or ""
        qt = it.get("qtype") or "essay"
        opts = _json.dumps(it.get("options") or [], ensure_ascii=False)
        cor = it.get("correct") or ""
        topic = it.get("topic") or ""
        if not USE_DB:
            old = _MEM["srs"].get((user_id, cid))
            if old is None:
                streak, interval_d = (1, 1) if result == "ok" else (0, 0)
                _MEM["srs"][(user_id, cid)] = {
                    "id": cid, "user_id": user_id, "question": q, "answer": ans, "qtype": qt,
                    "options": opts, "correct": cor, "topic": topic,
                    "streak": streak, "interval_d": interval_d,
                    "next_at": now + interval_d * 86400, "updated_at": now}
                added += 1
            elif result == "ok":
                old.update({"answer": ans, "qtype": qt, "options": opts, "correct": cor,
                            "topic": topic, "updated_at": now})
                old["streak"] = old.get("streak", 0) + 1
                old["interval_d"] = _SRS_INTERVALS[min(old["streak"], len(_SRS_INTERVALS)) - 1]
                old["next_at"] = now + old["interval_d"] * 86400
                updated += 1
            elif result == "bad":
                old.update({"answer": ans, "qtype": qt, "options": opts, "correct": cor,
                            "topic": topic, "updated_at": now})
                old["streak"] = 0
                old["interval_d"] = 0
                old["next_at"] = now
                updated += 1
        else:
            conn = _conn()
            cur = conn.cursor()
            cur.execute("SELECT streak FROM srs_cards WHERE id=? AND user_id=?", (cid, user_id))
            ex = cur.fetchone()
            if ex is None:
                streak, interval_d = (1, 1) if result == "ok" else (0, 0)
                cur.execute(
                    "INSERT INTO srs_cards (id,user_id,question,answer,qtype,options,correct,topic,"
                    "streak,interval_d,next_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, user_id, q, ans, qt, opts, cor, topic, streak, interval_d,
                     now + interval_d * 86400, now))
                added += 1
            elif result == "ok":
                streak = ex[0] + 1
                interval_d = _SRS_INTERVALS[min(streak, len(_SRS_INTERVALS)) - 1]
                cur.execute(
                    "UPDATE srs_cards SET answer=?,qtype=?,options=?,correct=?,topic=?,streak=?,"
                    "interval_d=?,next_at=?,updated_at=? WHERE id=? AND user_id=?",
                    (ans, qt, opts, cor, topic, streak, interval_d,
                     now + interval_d * 86400, now, cid, user_id))
                updated += 1
            elif result == "bad":
                cur.execute(
                    "UPDATE srs_cards SET answer=?,qtype=?,options=?,correct=?,topic=?,streak=0,"
                    "interval_d=0,next_at=?,updated_at=? WHERE id=? AND user_id=?",
                    (ans, qt, opts, cor, topic, now, now, cid, user_id))
                updated += 1
            conn.commit()
            conn.close()
    return {"added": added, "updated": updated}


def srs_due(user_id: str, limit: int = 100) -> list:
    """取今日到期卡片（next_at <= now）。"""
    import json as _json
    now = time.time()
    if not USE_DB:
        rows = [v for (uid, _cid), v in _MEM["srs"].items()
                if uid == user_id and v.get("next_at", 0) <= now]
        rows.sort(key=lambda v: v.get("next_at", 0))
        return [{"id": v["id"], "question": v["question"], "answer": v.get("answer", ""),
                 "qtype": v.get("qtype", "essay"), "options": _json.loads(v.get("options") or "[]"),
                 "correct": v.get("correct", ""), "topic": v.get("topic", "")} for v in rows[:limit]]
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id,question,answer,qtype,options,correct,topic FROM srs_cards"
                " WHERE user_id=? AND next_at<=? ORDER BY next_at LIMIT ?", (user_id, now, limit))
    out = [{"id": r[0], "question": r[1], "answer": r[2], "qtype": r[3],
            "options": _json.loads(r[4] or "[]"), "correct": r[5], "topic": r[6]}
           for r in cur.fetchall()]
    conn.close()
    return out


def srs_stats(user_id: str) -> dict:
    if not USE_DB:
        vals = [v for (uid, _cid), v in _MEM["srs"].items() if uid == user_id]
        return {"total": len(vals), "due": len([1 for v in vals if v.get("next_at", 0) <= time.time()])}
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN next_at<=? THEN 1 ELSE 0 END) FROM srs_cards WHERE user_id=?",
                (time.time(), user_id))
    t, d = cur.fetchone()
    conn.close()
    return {"total": t or 0, "due": d or 0}


def srs_clear_user(user_id: str):
    if not USE_DB:
        for k in [k for k in _MEM["srs"] if k[0] == user_id]:
            _MEM["srs"].pop(k, None)
        return
    conn = _conn()
    conn.execute("DELETE FROM srs_cards WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
