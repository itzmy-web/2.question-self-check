# -*- coding: utf-8 -*-
"""历史记录接口回归测试（不联网、不消耗模型额度）。

覆盖：
  1. 历史记录接口必须登录（无 token / 错误 token -> 401）
  2. 保存文档 + 题目答案，列表能看到概要（不带正文）
  3. 取单条能拿到完整正文与 questions
  4. **用户隔离**：A 用户读不到、删不掉 B 用户的记录
  5. 保存做题总结（完成 / 中途结束），列表与明细正确
  6. 删除单条 / 清空全部
  7. 空内容不允许保存（400）
  8. 抽出题目里带 topic 字段（薄弱方面统计要用）
"""
import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["USE_SQLITE"] = "0"      # 内存模式，测试互不污染
os.environ["ACCESS_CODE"] = ""  # 显式关闭门槛（A4 后默认开启，测试专注测历史记录）

from fastapi.testclient import TestClient  # noqa: E402

import backend.main as m  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <-- {extra}"))


client = TestClient(m.app)


def login(phone):
    r = client.post("/api/auth/send-code", json={"phone": phone})
    code = r.json().get("demo_code")
    r = client.post("/api/auth/login", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


A = login("13800000001")
B = login("13800000002")
print("两个测试用户已登录\n")

print("[1] 必须登录")
check("docs 无 token -> 401", client.get("/api/history/docs").status_code == 401)
check("doc 保存无 token -> 401",
      client.post("/api/history/doc", json={"text": "x"}).status_code == 401)
check("sessions 无 token -> 401", client.get("/api/history/sessions").status_code == 401)
check("错误 token -> 401",
      client.get("/api/history/docs", headers={"Authorization": "Bearer nope"}).status_code == 401)

print("\n[2] A 保存文档 + 题目答案")
QA = [
    {"id": "q0", "question": "光合作用的场所是？", "answer": "叶绿体", "topic": "光合作用", "options": ["线粒体", "叶绿体"]},
    {"id": "q1", "question": "细胞膜的主要成分？", "answer": "磷脂和蛋白质", "topic": "细胞结构", "options": []},
]
r = client.post("/api/history/doc", headers=A,
                json={"filename": "生物笔记.pdf", "kind": "pdf",
                      "text": "光合作用在叶绿体中进行。细胞膜主要由磷脂和蛋白质构成。",
                      "questions": QA})
check("保存文档 -> 200", r.status_code == 200, r.text[:200])
doc_id = r.json().get("id")
check("返回 id", bool(doc_id), r.text[:200])

r = client.get("/api/history/docs", headers=A)
items = r.json().get("items") or []
check("列表有 1 条", len(items) == 1, items)
it = items[0] if items else {}
check("列表带 filename", it.get("filename") == "生物笔记.pdf", it)
check("列表带 qa_count=2", it.get("qa_count") == 2, it)
check("列表带 chars", it.get("chars", 0) > 0, it)
check("列表不带正文（省流量）", "text" not in it, list(it.keys()))

print("\n[3] A 取单条完整记录")
r = client.get(f"/api/history/doc/{doc_id}", headers=A)
d = r.json().get("item") or {}
check("取单条 -> 200", r.status_code == 200, r.status_code)
check("含完整正文", "叶绿体" in (d.get("text") or ""), (d.get("text") or "")[:60])
check("含 questions", len(d.get("questions") or []) == 2, d.get("questions"))
check("questions 保留 topic", (d["questions"][0].get("topic") == "光合作用"), d["questions"][0])
check("不返回内部 qa 字段", "qa" not in d, list(d.keys()))

print("\n[4] 用户隔离（关键）")
check("B 的列表为空（看不到 A 的）", (client.get("/api/history/docs", headers=B).json() or {}).get("items") == [])
check("B 取 A 的单条 -> 404", client.get(f"/api/history/doc/{doc_id}", headers=B).status_code == 404)
check("B 删 A 的记录 -> ok=False", client.delete(f"/api/history/doc/{doc_id}", headers=B).json().get("ok") is False)
check("A 的记录仍在", len(client.get("/api/history/docs", headers=A).json()["items"]) == 1)

print("\n[5] A 保存做题总结")
SES = {
    "doc_label": "生物笔记.pdf",
    "total": 2, "answered": 2, "ok": 1, "bad": 1,
    "choice_ok": 1, "choice_bad": 0, "score": 10, "max_score": 20,
    "finished": True,
    "aspects": [{"topic": "光合作用", "total": 1, "ok": 1, "bad": 0, "rate": 100},
                {"topic": "细胞结构", "total": 1, "ok": 0, "bad": 1, "rate": 0}],
    "detail": [{"question": "细胞膜的主要成分？", "topic": "细胞结构", "result": "unfam"}],
}
r = client.post("/api/history/session", headers=A, json=SES)
check("保存总结 -> 200", r.status_code == 200, r.text[:200])
ses_id = r.json().get("id")
check("返回 id", bool(ses_id))

r = client.get("/api/history/sessions", headers=A)
sitems = r.json().get("items") or []
check("总结列表 1 条", len(sitems) == 1, sitems)
s0 = sitems[0] if sitems else {}
check("列表带 finished=True", s0.get("finished") == 1, s0)
check("列表带 bad=1", s0.get("bad") == 1, s0)
check("列表不含 aspects 明细", "aspects" not in s0, list(s0.keys()))

r = client.get(f"/api/history/session/{ses_id}", headers=A)
sd = r.json().get("item") or {}
check("明细含 aspects", len(sd.get("aspects") or []) == 2, sd.get("aspects"))
check("明细 aspects 主题正确",
      sorted(a["topic"] for a in sd["aspects"]) == ["光合作用", "细胞结构"], sd.get("aspects"))
check("明细含 detail", len(sd.get("detail") or []) == 1, sd.get("detail"))
check("B 取 A 的总结 -> 404",
      client.get(f"/api/history/session/{ses_id}", headers=B).status_code == 404)

print("\n[5b] 中途结束（finished=False）")
r = client.post("/api/history/session", headers=A,
                json={"doc_label": "半途", "total": 10, "answered": 3, "ok": 2, "bad": 1,
                      "score": 20, "max_score": 100, "finished": False})
mid_id = r.json().get("id")
check("中途结束也能存", r.status_code == 200, r.status_code)
sd = client.get(f"/api/history/session/{mid_id}", headers=A).json()["item"]
check("finished=0 被保留", sd.get("finished") == 0, sd.get("finished"))
check("总结共 2 条", len(client.get("/api/history/sessions", headers=A).json()["items"]) == 2)

print("\n[6] 删除 / 清空")
check("A 删自己的文档 -> ok=True",
      client.delete(f"/api/history/doc/{doc_id}", headers=A).json().get("ok") is True)
check("删除后列表 0 条", client.get("/api/history/docs", headers=A).json()["items"] == [])
check("重复删除 -> ok=False",
      client.delete(f"/api/history/doc/{doc_id}", headers=A).json().get("ok") is False)
r = client.delete("/api/history", headers=A)
check("清空全部 -> 200", r.status_code == 200, r.status_code)
check("清空移除 1 条总结", r.json()["removed"]["sessions"] == 2, r.json())
check("清空后 sessions 为空", client.get("/api/history/sessions", headers=A).json()["items"] == [])

print("\n[7] 空内容不允许保存")
check("空 text + 空 questions -> 400",
      client.post("/api/history/doc", headers=A, json={"text": "", "questions": []}).status_code == 400)
check("只有 questions 无 text -> 200",
      client.post("/api/history/doc", headers=A,
                  json={"filename": "仅题目", "text": "", "questions": QA}).status_code == 200)

print("\n[8] 抽题返回 topic 字段（用假模型函数验证解析，不联网）")
orig = m._chat_text
m._chat_text = lambda *a, **k: (
    '[{"question":"光合作用的场所是？","answer":"叶绿体","topic":"光合作用","options":["线粒体","叶绿体"]},'
    '{"question":"简述细胞膜结构","answer":"磷脂双分子层","topic":"细胞结构","options":[]}]'
)
m.LLM_API_KEY = m.LLM_API_KEY or "dummy"
r = client.post("/api/extract", json={"text": "随便一段内容"})
m._chat_text = orig
check("extract -> 200", r.status_code == 200, r.text[:200])
qs = r.json().get("questions") or []
check("解析出 2 题", len(qs) == 2, qs)
check("第 1 题带 topic=光合作用", qs and qs[0].get("topic") == "光合作用", qs[:1])
check("第 2 题带 topic=细胞结构", len(qs) > 1 and qs[1].get("topic") == "细胞结构", qs[1:2])
check("缺 topic 时不报错（回退空串）", all("topic" in q for q in qs), qs)

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过 ✅")
