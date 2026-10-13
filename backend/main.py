# -*- coding: utf-8 -*-
"""
问题自检 · 后端服务（FastAPI）

功能：
  - 手机号注册/登录（发码 + 校验），数据持久化到 SQLite
  - 微信 / 支付宝 扫码登录：双模式
        * 演示模式（默认）：ticket + 状态轮询 + confirm 模拟扫码授权，零依赖、可立即联调
        * 真实 OAuth：配置开放平台 AppID/密钥后，生成真实授权 URL，回调换取 openid 后落库
  - /api/auth/me 返回当前登录用户
  - /api/auth/config 暴露各 provider 当前模式（demo / oauth）
  - 托管前端静态文件（login.html / index.html）

环境变量（真实 OAuth 需要）：
  WX_APPID / WX_SECRET            微信开放平台 网站应用 AppID / AppSecret
  ALI_APPID / ALI_PRIVATE_KEY        支付宝 网站应用 AppID / 应用私钥（RSA2）
  ALI_PUBLIC_KEY                  支付宝 公钥（换取 userinfo 时校验用，可选）
  PUBLIC_BASE_URL                 对外可访问的站点根地址，用于拼接 OAuth 回调域名
  USE_SQLITE                      设 "0" 可关闭持久化（回退纯内存演示）
  ACCESS_CODE                     访问码；不设 = 关闭门槛（默认关闭），设了就开启防护
  LLM_BASE_URL / LLM_API_KEY      SiliconFlow（OpenAI 兼容）端点与密钥
  LLM_TEXT_MODEL / LLM_VISION_MODEL  抽题模型 / 图片 OCR 视觉模型

未配置上述密钥时，所有扫码登录自动走演示模式，页面照常可用。
"""
import asyncio
import html as _html
import io
import os
import shutil
import time
import uuid
import secrets
import threading
import urllib.parse
import urllib.request
import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from . import db  # uvicorn backend.main:app（包方式导入）
except ImportError:
    import db     # 兼容 python backend/main.py 直接运行

BASE_DIR = Path(__file__).resolve().parent.parent  # self-check-quiz/
FRONT_DIR = BASE_DIR  # login.html / index.html 所在目录

app = FastAPI(title="问题自检后端", version="1.1.0")

db.init_db()

# ---------------- 配置：第三方登录 ----------------
WX_APPID = os.getenv("WX_APPID")
WX_SECRET = os.getenv("WX_SECRET")
ALI_APPID = os.getenv("ALI_APPID")
ALI_PRIVATE_KEY = os.getenv("ALI_PRIVATE_KEY")
ALI_PUBLIC_KEY = os.getenv("ALI_PUBLIC_KEY")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

CODE_TTL = 300   # 验证码 5 分钟
QR_TTL = 120     # 二维码 2 分钟
DEMO_CODE = os.getenv("DEMO_CODE") or None  # 固定演示码（如 123456）；不设则每次随机
# 演示模式（未接短信网关）把验证码回传给前端显示，避免用户只能去服务端日志里找
SHOW_DEMO_CODE = os.getenv("SHOW_DEMO_CODE", "1") != "0"

# ---------------- LLM 默认配置（服务端统一；前端不再接收/直传 apiKey） ----------------
# A3：默认对接 SiliconFlow（OpenAI 兼容）。凭据一律从部署环境变量注入，
# 禁止在源码/前端写死 Key；前端传入的 apiKey 将被服务端忽略。
LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or "https://api.siliconflow.cn/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY") or ""   # A3：无默认值，缺失时调用报错提示配置
if not LLM_API_KEY:
    # B3：云沙箱无法注入环境变量时的兜底——从 backend/llm_key.txt 读取（该目录
    # 被静态托管白名单拦截，公网不可下载）。优先级：环境变量 > 密钥文件 > 无。
    try:
        _key_file = Path(__file__).resolve().parent / "llm_key.txt"
        if _key_file.exists():
            LLM_API_KEY = _key_file.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
LLM_TEXT_MODEL = os.getenv("LLM_TEXT_MODEL") or "Qwen/Qwen3.8-27B"          # 抽题/生成选项
LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL") or "Qwen/Qwen3-VL-30B-A3B-Instruct"  # 图片 OCR

# ---------------- 访问码（公开链接防白嫖；默认开启） ----------------
# A4：访问码默认开启——未设置环境变量 ACCESS_CODE 时，自动生成随机访问码
# 并在启动日志打印（首次进入页面需输入该码）；显式设空字符串 = 显式关闭门槛。
# 页面会调用服务端的大模型额度，链接一旦外泄，任何人都能拿你的额度跑模型。
# 推荐部署时设环境变量 ACCESS_CODE=<你的码>：前 4 类接口要求请求头带 X-Access-Code。
#   前端首次进入时输入一次，存 localStorage，之后每次请求自动带 X-Access-Code 头。
_ACCESS_ENV = os.getenv("ACCESS_CODE")
# B4（2026-10-13）：应用户要求默认关闭访问码——平台将自部署到用户自有服务器，
# 由服务器侧网关/内网鉴权兜底。需要门槛时设环境变量 ACCESS_CODE=<你的码> 即可开启。
if _ACCESS_ENV is None:
    ACCESS_CODE = ""
else:
    ACCESS_CODE = _ACCESS_ENV.strip()         # 显式设置（空字符串 = 显式关闭）
ACCESS_MAX_FAILS = 20        # 同一 IP 在窗口内最多允许失败次数
ACCESS_FAIL_WINDOW = 600     # 失败计数窗口（秒）
_ACCESS_FAILS = {}           # ip -> [失败次数, 窗口起始时间]
_ACCESS_FAILS_LOCK = threading.Lock()
if ACCESS_CODE:
    print(f"[ACCESS] 访问码保护已启用（环境变量 ACCESS_CODE 可修改）：{ACCESS_CODE}")
else:
    print("[ACCESS] 访问码保护已显式关闭（ACCESS_CODE 设为空字符串）")


# ---------------- 请求体 ----------------
class SendCodeReq(BaseModel):
    phone: str


class LoginReq(BaseModel):
    phone: str
    code: str


class ConfirmReq(BaseModel):
    ticket: str


# ---------------- 工具 ----------------
def now():
    return time.time()


def gen_token():
    return secrets.token_hex(24)


def phone_ok(phone: str) -> bool:
    return bool(phone and len(phone) == 11 and phone[0] == "1" and phone.isdigit())


def provider_mode(provider: str) -> str:
    """返回该 provider 当前是 'oauth'（已配置真实密钥）还是 'demo'（演示）。"""
    if provider == "wx":
        return "oauth" if (WX_APPID and WX_SECRET) else "demo"
    if provider == "ali":
        return "oauth" if (ALI_APPID and ALI_PRIVATE_KEY) else "demo"
    return "demo"


def find_or_create_user(provider: str, identifier: str, name=None, avatar=None):
    user = db.get_user_by_provider(provider, identifier)
    if user:
        if name and (user.get("name") != name or (avatar and user.get("avatar") != avatar)):
            db.update_user_name(user["id"], name, avatar)
            user = db.get_user_by_id(user["id"])
        return user
    return db.create_user(identifier, provider, name, avatar)


def http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "qc-backend/1.1"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------- 访问码：工具与校验 ----------------
def _client_ip(request: Request) -> str:
    """取真实客户端 IP（部署在一层反代后时优先用 X-Forwarded-For）。"""
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    return request.client.host if request.client else "unknown"


def _access_locked(ip: str) -> bool:
    """该 IP 是否因失败过多被临时锁定；顺带清理过期记录防内存膨胀。"""
    t = now()
    with _ACCESS_FAILS_LOCK:
        if len(_ACCESS_FAILS) > 500:
            for k in [k for k, v in _ACCESS_FAILS.items() if t - v[1] > ACCESS_FAIL_WINDOW]:
                _ACCESS_FAILS.pop(k, None)
        rec = _ACCESS_FAILS.get(ip)
        return bool(rec and t - rec[1] <= ACCESS_FAIL_WINDOW and rec[0] >= ACCESS_MAX_FAILS)


def _access_bump(ip: str):
    """记一次失败。"""
    t = now()
    with _ACCESS_FAILS_LOCK:
        rec = _ACCESS_FAILS.get(ip)
        if not rec or t - rec[1] > ACCESS_FAIL_WINDOW:
            _ACCESS_FAILS[ip] = [1, t]
        else:
            rec[0] += 1


def _code_matches(given) -> bool:
    """定长比较，避免时序侧信道；未配置访问码时一律放行。"""
    if not ACCESS_CODE:
        return True
    return secrets.compare_digest(
        str(given or "").strip().encode("utf-8"), ACCESS_CODE.encode("utf-8")
    )


def require_access(request: Request):
    """校验请求头 X-Access-Code；不通过抛 401，失败过多抛 429。"""
    if not ACCESS_CODE:
        return
    ip = _client_ip(request)
    if _access_locked(ip):
        raise HTTPException(status_code=429, detail="访问码错误次数过多，请稍后再试")
    if _code_matches(request.headers.get("X-Access-Code", "")):
        with _ACCESS_FAILS_LOCK:
            _ACCESS_FAILS.pop(ip, None)
        return
    _access_bump(ip)
    raise HTTPException(status_code=401, detail="访问码不正确，请重新输入")


class AccessReq(BaseModel):
    code: str = ""


@app.post("/api/access/verify")
async def access_verify(req: AccessReq, request: Request):
    """前端校验访问码是否正确。纯字符串比对，不消耗任何模型额度。"""
    if not ACCESS_CODE:
        return {"ok": True, "required": False}
    ip = _client_ip(request)
    if _access_locked(ip):
        raise HTTPException(status_code=429, detail="访问码错误次数过多，请稍后再试")
    if not _code_matches(req.code):
        _access_bump(ip)
        raise HTTPException(status_code=401, detail="访问码不正确，请重新输入")
    with _ACCESS_FAILS_LOCK:
        _ACCESS_FAILS.pop(ip, None)
    return {"ok": True, "required": True}


# ---------------- 手机号：发码 ----------------
@app.post("/api/auth/send-code")
async def send_code(req: SendCodeReq):
    if not phone_ok(req.phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    code = DEMO_CODE or f"{secrets.randbelow(900000) + 100000:06d}"
    db.save_phone_code(req.phone, code, now() + CODE_TTL)
    # 真实环境此处调用短信网关；演示直接打印到服务端日志
    print(f"[DEMO] 向 {req.phone} 发送验证码：{code}")
    resp = {"ok": True, "message": "验证码已发送（演示）"}
    # 演示环境把验证码直接回传前端，方便登录
    if SHOW_DEMO_CODE:
        resp["demo_code"] = code
    return resp


# ---------------- 手机号：登录/注册 ----------------
@app.post("/api/auth/login")
async def login(req: LoginReq):
    if not phone_ok(req.phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    rec = db.get_phone_code(req.phone)
    if not rec or rec["exp"] < now():
        raise HTTPException(status_code=400, detail="验证码已过期，请重新获取")
    if DEMO_CODE is None and rec["code"] != req.code:
        raise HTTPException(status_code=400, detail="验证码错误")
    user = find_or_create_user("phone", req.phone)
    token = gen_token()
    db.save_token(token, user["id"])
    return {"ok": True, "token": token, "user": user}


# ---------------- 游客体验：一键创建临时账号（按浏览器隔离，无需注册） ----------------
@app.post("/api/auth/guest")
async def guest_login():
    gid = "guest_" + uuid.uuid4().hex[:12]
    user = find_or_create_user("guest", gid, name="游客")
    token = gen_token()
    db.save_token(token, user["id"])
    return {"ok": True, "token": token, "user": user, "guest": True}


# ---------------- 账号密码登录（企业版：演示阶段任意账号/密码≥4位即可创建并登录） ----------------
class PasswordLoginReq(BaseModel):
    account: str
    password: str


@app.post("/api/auth/password-login")
async def password_login(req: PasswordLoginReq):
    acc = (req.account or "").strip()
    pwd = (req.password or "").strip()
    if len(acc) < 2 or len(acc) > 40:
        raise HTTPException(status_code=400, detail="账号需为 2-40 个字符")
    if len(pwd) < 4 or len(pwd) > 64:
        raise HTTPException(status_code=400, detail="密码至少 4 位")
    user = find_or_create_user("pwd", acc, name=acc)
    token = gen_token()
    db.save_token(token, user["id"])
    return {"ok": True, "token": token, "user": user}


class MigrateReq(BaseModel):
    guest_token: str = ""


@app.post("/api/auth/migrate")
async def auth_migrate(req: MigrateReq, request: Request):
    """游客升级：登录正式账号后，把浏览器里游客身份的文档/做题历史并入新账号。"""
    require_access(request)
    if not req.guest_token.strip():
        return {"ok": True, "migrated": 0}
    gu = db.get_user_by_token(req.guest_token.strip())
    if not gu or gu.get("provider") != "guest":
        return {"ok": True, "migrated": 0}
    auth = (request.headers.get("authorization") or "").replace("Bearer ", "").strip()
    u = db.get_user_by_token(auth)
    if not u or u.get("id") == gu.get("id"):
        return {"ok": True, "migrated": 0}
    moved = db.migrate_user_data(gu["id"], u["id"])
    return {"ok": True, "migrated": 1, **moved}


# ---------------- SRS 间隔重复：错题/到期卡自动调度，「今日复习」数据源 ----------------
class SrsItem(BaseModel):
    question: str = ""
    answer: str = ""
    qtype: str = "essay"
    options: list = []
    correct: str = ""
    topic: str = ""
    result: str = "skip"


class SrsIngestReq(BaseModel):
    items: list = []


@app.post("/api/srs/ingest")
async def srs_ingest(req: SrsIngestReq, request: Request):
    """每次做题总结保存时调用：把本轮题目喂进复习计划并按结果调度。"""
    user = _require_user(request)
    items = [i if isinstance(i, dict) else {} for i in (req.items or [])]
    r = db.srs_ingest(user["id"], items)
    return {"ok": True, **r}


@app.get("/api/srs/due")
async def srs_due(request: Request, limit: int = 100):
    """今日到期卡片（next_at<=now），供「今日复习」翻卡。"""
    user = _require_user(request)
    cards = db.srs_due(user["id"], limit=max(1, min(limit, 200)))
    stats = db.srs_stats(user["id"])
    return {"ok": True, "cards": cards, "due": len(cards), "total": stats["total"]}


@app.get("/api/srs/stats")
async def srs_stats_ep(request: Request):
    user = _require_user(request)
    return {"ok": True, **db.srs_stats(user["id"])}


# ---------------- LLM 抽题额度：按用户每天限次，防止被脚本刷穿模型账单 ----------------
_LLM_QUOTA = {}          # uid -> [date_str, count]
_LLM_DAILY_LIMIT = 100   # 每用户每日 LLM 抽题提交上限


def _llm_quota_check(request: Request):
    auth = (request.headers.get("authorization") or "").replace("Bearer ", "").strip()
    u = db.get_user_by_token(auth)
    uid = u["id"] if u else "ip:" + (request.client.host if request.client else "unknown")
    today = time.strftime("%Y-%m-%d")
    rec = _LLM_QUOTA.get(uid)
    if not rec or rec[0] != today:
        rec = _LLM_QUOTA[uid] = [today, 0]
    if rec[1] >= _LLM_DAILY_LIMIT:
        raise HTTPException(status_code=429,
                            detail=f"今日 AI 额度已用完（每用户每天 {_LLM_DAILY_LIMIT} 次），明天再来；抽题可改用「本地规则」模式（不消耗额度）")
    rec[1] += 1


# ---------------- 扫码：生成 ticket + 二维码内容（双模式） ----------------
@app.get("/api/auth/{provider}/qrcode")
async def qrcode(provider: str):
    if provider not in ("wx", "ali"):
        raise HTTPException(status_code=400, detail="不支持的登录方式")
    mode = provider_mode(provider)
    ticket = uuid.uuid4().hex
    db.save_qr(ticket, provider, now() + QR_TTL)

    if mode == "oauth":
        # 真实模式：二维码内容 = 开放平台授权页 URL（state 携带 ticket 用于回调认领）
        state = ticket
        redirect = urllib.parse.quote(f"{PUBLIC_BASE_URL}/api/auth/{provider}/callback")
        if provider == "wx":
            url = (
                "https://open.weixin.qq.com/connect/qrconnect"
                f"?appid={WX_APPID}&redirect_uri={redirect}"
                "&response_type=code&scope=snsapi_login"
                f"&state={state}#wechat_redirect"
            )
        else:
            url = (
                "https://openauth.alipay.com/oauth2/publicAppAuthorize.htm"
                f"?app_id={ALI_APPID}&scope=auth_user&state={state}"
                f"&redirect_uri={redirect}"
            )
    else:
        # 演示模式：二维码编码 confirm 接口地址，手机扫码即触发 confirm
        url = f"/api/auth/{provider}/confirm?ticket={ticket}"

    return {"ok": True, "ticket": ticket, "url": url, "mode": mode, "exp": QR_TTL}


# ---------------- 扫码：轮询状态 ----------------
@app.get("/api/auth/{provider}/status")
async def status(provider: str, ticket: str = Query(...)):
    s = db.get_qr(ticket)
    if not s or s["provider"] != provider:
        raise HTTPException(status_code=404, detail="二维码不存在或已失效")
    if s["exp"] < now():
        raise HTTPException(status_code=410, detail="二维码已过期，请刷新")
    if s["status"] == "confirmed" and s.get("token"):
        user = db.get_user_by_id(s["user_id"])
        if user and s["token"]:
            return {"status": "confirmed", "token": s["token"], "user": user}
    return {"status": s["status"]}


# ---------------- 扫码：演示模式确认（模拟手机端扫码授权） ----------------
@app.post("/api/auth/{provider}/confirm")
async def confirm(provider: str, req: ConfirmReq):
    if provider_mode(provider) != "demo":
        raise HTTPException(status_code=400, detail="该方式已启用真实 OAuth，演示确认不可用")
    s = db.get_qr(req.ticket)
    if not s or s["provider"] != provider:
        raise HTTPException(status_code=404, detail="二维码不存在或已失效")
    if s["exp"] < now():
        raise HTTPException(status_code=410, detail="二维码已过期，请刷新")
    if s["status"] == "confirmed":
        return {"ok": True}
    user = find_or_create_user(provider, req.ticket)
    token = gen_token()
    db.save_token(token, user["id"])
    db.confirm_qr(req.ticket, user, token)
    return {"ok": True, "token": token, "user": user}


# ---------------- 真实 OAuth 回调（微信，标准库即可打通） ----------------
@app.get("/api/auth/wx/callback")
async def wx_callback(code: str = Query(None), state: str = Query(None)):
    s = db.get_qr(state) if state else None
    if not s:
        return HTMLResponse("<h3>登录失败：二维码已失效，请在电脑端刷新后重试。</h3>")
    if s["exp"] < now():
        return HTMLResponse("<h3>登录失败：二维码已过期，请在电脑端刷新后重试。</h3>")
    try:
        token_url = (
            "https://api.weixin.qq.com/sns/oauth2/access_token"
            f"?appid={WX_APPID}&secret={WX_SECRET}&code={code}&grant_type=authorization_code"
        )
        tok = http_get_json(token_url)
        if "errcode" in tok:
            return HTMLResponse(f"<h3>微信授权失败：{_html.escape(str(tok.get('errmsg')))}</h3>")
        openid = tok["openid"]
        access = tok["access_token"]
        info = http_get_json(
            "https://api.weixin.qq.com/sns/userinfo"
            f"?access_token={access}&openid={openid}"
        )
        name = info.get("nickname")
        avatar = info.get("headimgurl")
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<h3>微信授权异常：{_html.escape(str(e))}</h3>")

    user = find_or_create_user("wx", openid, name, avatar)
    token = gen_token()
    db.save_token(token, user["id"])
    db.confirm_qr(state, user, token)
    return HTMLResponse(
        "<h3>✅ 微信登录成功，请返回电脑端继续。</h3>"
        "<p style='color:#888'>你可以关闭此页面。</p>"
    )


# ---------------- 真实 OAuth 回调（支付宝，需要 alipay SDK 完成签名换 token） ----------------
@app.get("/api/auth/ali/callback")
async def ali_callback(app_id: str = Query(None), source: str = Query(None),
                       auth_code: str = Query(None), state: str = Query(None)):
    s = db.get_qr(state) if state else None
    if not s:
        return HTMLResponse("<h3>登录失败：二维码已失效，请在电脑端刷新后重试。</h3>")
    if s["exp"] < now():
        return HTMLResponse("<h3>登录失败：二维码已过期，请在电脑端刷新后重试。</h3>")
    # 支付宝需使用应用私钥对请求做 RSA2 签名后换取 access_token，
    # 标准库无现成 RSA 签名实现，故依赖官方 alipay-sdk-python。
    try:
        from alipay import AliPay  # type: ignore
    except Exception:  # noqa: BLE001
        return HTMLResponse(
            "<h3>支付宝真实登录未启用</h3>"
            "<p>请先 <code>pip install alipay-sdk-python</code> 并配置 "
            "ALI_APPID / ALI_PRIVATE_KEY / ALI_PUBLIC_KEY 后重试；"
            "当前仍可使用演示模式登录。</p>"
        )
    alipay = AliPay(appid=ALI_APPID, app_private_key_string=ALI_PRIVATE_KEY,
                    alipay_public_key_string=ALI_PUBLIC_KEY, sign_type="RSA2")
    try:
        resp = alipay.api_alipay_system_oauth_token(code=auth_code, grant_type="authorization_code")
        access = resp.get("access_token")
        user_info = alipay.api_alipay_user_info_share(access)
        openid = user_info.get("user_id")
        name = user_info.get("nick_name")
        avatar = user_info.get("avatar")
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<h3>支付宝授权异常：{_html.escape(str(e))}</h3>")

    user = find_or_create_user("ali", openid, name, avatar)
    token = gen_token()
    db.save_token(token, user["id"])
    db.confirm_qr(state, user, token)
    return HTMLResponse(
        "<h3>✅ 支付宝登录成功，请返回电脑端继续。</h3>"
        "<p style='color:#888'>你可以关闭此页面。</p>"
    )


# ---------------- 当前用户 ----------------
def get_user_from_header(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    return db.get_user_by_token(token)


@app.get("/api/auth/me")
async def me(request: Request):
    user = get_user_from_header(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "未登录"})
    return {"user": user}


# ---------------- 配置暴露（前端据此切换演示/真实 UI） ----------------
@app.get("/api/auth/config")
async def config():
    return {
        "phone": "enabled",
        "wx": provider_mode("wx"),
        "ali": provider_mode("ali"),
        "oauth_ready": bool(PUBLIC_BASE_URL),
    }


# ==================================================================
# 文档导入：异步任务（PDF / Word / 文本 / 图片 OCR）
#   前端上传后立刻拿到 job_id，后台线程解析，前端轮询 /api/job/{id}
#   图片走视觉模型 OCR，并顺带做「清晰度」判定：
#   不清楚 -> 返回 warn 提示用户，但依然返回尽力识别出的文字
# ==================================================================
import base64
import concurrent.futures as _cf
import re as _re

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 单个文件上限 200MB（支持大文件）
JOB_TTL = 1800.0                       # 任务结果保留 30 分钟

JOBS = {}
_JOBS_LOCK = threading.Lock()
_EXECUTOR = _cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="docjob")

_IMG_EXT = ("png", "jpg", "jpeg", "webp", "bmp", "gif", "heic")
_TXT_EXT = ("txt", "md", "csv", "json", "text", "log")


def _job_new(kind, filename):
    jid = uuid.uuid4().hex
    with _JOBS_LOCK:
        JOBS[jid] = {
            "status": "processing", "stage": "queued",
            "kind": kind, "filename": filename,
            "text": "", "warn": None, "note": "", "error": None,
            "created": now(), "updated": now(),
        }
    return jid


def _job_update(jid, **kw):
    with _JOBS_LOCK:
        j = JOBS.get(jid)
        if j is None:
            return
        j.update(kw)
        j["updated"] = now()


def _job_get(jid):
    with _JOBS_LOCK:
        j = JOBS.get(jid)
        return dict(j) if j else None


def _cleanup_jobs():
    """清理过期任务，避免长时间运行后内存膨胀。"""
    exp = now() - JOB_TTL
    with _JOBS_LOCK:
        for k in [k for k, v in JOBS.items() if v.get("updated", 0) < exp]:
            JOBS.pop(k, None)


def _kind_of(filename, explicit):
    """判定文件类型：pdf / docx / text / image / unsupported / doc_unsupported"""
    ext = ""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
    e = (explicit or "").lower()
    if e in ("photo", "camera", "image", "img"):
        return "image"
    if ext == "pdf" or e == "pdf":
        return "pdf"
    if ext == "docx" or e in ("word", "docx"):
        return "docx"
    if ext == "doc":
        return "doc_unsupported"
    if ext in _IMG_EXT:
        return "image"
    if ext in _TXT_EXT or e == "text":
        return "text"
    return "unsupported"


# ---------------- 各类解析 ----------------
# 正文降噪：过滤页码 / 每页重复的页眉页脚 / 目录点线行 / 纯符号行，只留关键信息
_PURE_PAGE_NO = _re.compile(
    r'^\s*[-—––_·.]?\s*(?:第\s*\d+\s*页(?:\s*[,，]?\s*共\s*\d+\s*页)?|共\s*\d+\s*页'
    r'|Page\s*\d+(?:\s*(?:of|/)\s*\d+)?|\d+\s*/\s*\d+|\d{1,4})\s*[-—––_·.]?\s*$',
    _re.I)
_DOT_LEADER = _re.compile(r'[.．·•…]{6,}')


def _is_noise_line(l, repeated):
    if not l:
        return True
    if l.startswith('|') or l.startswith('#'):      # 表格行 / 标题行：一律保留
        return False
    if l in repeated:                               # 每页重复出现的相同短行 → 页眉页脚
        return True
    if _PURE_PAGE_NO.match(l):                      # 页码（第 X 页 / 12 / 3/12 / Page 3）
        return True
    if len(l) <= 60 and _DOT_LEADER.search(l):      # 目录点线行（标题………5）
        return True
    if not _re.search(r'[\u4e00-\u9fffA-Za-z0-9]', l):  # 没有任何文字的符号/装饰行
        return True
    return False


def _repeated_lines(line_groups, min_frac=0.6):
    """在大多数页里都原样出现的短行 → 判定为页眉/页脚。"""
    n = len(line_groups)
    if n < 3:
        return set()
    from collections import Counter
    c = Counter()
    for g in line_groups:
        seen = set()
        for l in g:
            l = (l or '').strip()
            if l and not l.startswith('|') and not l.startswith('#') and len(l) <= 40:
                seen.add(l)
        c.update(seen)
    return {l for l, k in c.items() if k >= max(3, int(n * min_frac))}


def _denoise_lines(line_groups):
    """line_groups: 每页一组的行列表；返回降噪后的扁平行列表。"""
    repeated = _repeated_lines(line_groups)
    out = []
    for g in line_groups:
        for l in g:
            ls = (l or '').strip()
            if not _is_noise_line(ls, repeated):
                out.append(ls)
    return out


def _extract_plain(data):
    for enc in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            continue
    else:
        text = data.decode("utf-8", "ignore")
    raw = text.strip()
    return "\n".join(_denoise_lines([raw.split("\n")])).strip() or raw


def _extract_pdf(data):
    import io as _io
    reader = None
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(_io.BytesIO(data))
    except ImportError:
        raise RuntimeError(
            "缺少 PDF 解析依赖。请在项目根目录执行：`pip install pypdf` "
            "（或重新安装依赖：`pip install -r requirements.txt`）后重启服务。"
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"PDF 解析失败：{e}")
    pages = []
    try:
        for pg in reader.pages:
            try:
                t = pg.extract_text() or ""
            except Exception:  # noqa: BLE001
                t = ""
            pages.append(t.split("\n"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"PDF 读取失败：{e}")
    raw = "\n".join("\n".join(pg) for pg in pages).strip()
    if not raw:
        raise RuntimeError(
            "这个 PDF 里没有提取到文字，多半是「扫描件 / 图片型 PDF」。"
            "请改用「拍照 / 相册」用 OCR 识别，或先转成 Word / TXT 再上传。"
        )
    text = "\n".join(_denoise_lines(pages)).strip()
    return text or raw   # 降噪过度时回退原文，保证不丢内容


def _extract_docx(data):
    import io as _io
    try:
        import docx  # python-docx  # type: ignore
    except ImportError:
        raise RuntimeError(
            "缺少 Word 解析依赖。请执行：`pip install python-docx` "
            "（或 `pip install -r requirements.txt`）后重启服务。"
        )
    try:
        d = docx.Document(_io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Word 解析失败：{e}（若是加密/损坏文档请先另存为新文件）")
    from docx.table import Table as _DocxTable  # type: ignore

    # 按文档真实顺序产出「段落 + 表格」混合流；旧版分别遍历会打乱顺序、压平表格。
    blocks = []
    try:
        if hasattr(d, "iter_inner_content"):
            blocks = list(d.iter_inner_content())
    except Exception:  # noqa: BLE001
        blocks = []
    if not blocks:
        blocks = list(d.paragraphs) + list(d.tables)

    parts = []
    for block in blocks:
        if isinstance(block, _DocxTable):
            md = _render_table_md(block)
            if md:
                parts.append(md)
        else:
            md = _para_markdown(block)
            if md:
                parts.append(md)

    raw = "\n\n".join(parts).strip()
    if not raw:
        raise RuntimeError("这个 Word 文档里没有提取到正文文字（可能是图片型文档），请改用「拍照 / 相册」识别。")
    # 降噪：逐块过滤页码 / 目录点线 / 纯符号行，保留块间空行（预览渲染依赖空行分块）
    cleaned = []
    for p in parts:
        ls = _denoise_lines([p.split("\n")])
        if ls:
            cleaned.append("\n".join(ls))
    text = "\n\n".join(cleaned).strip()
    return text or raw


def _para_markdown(p):
    """单个段落 → 带样式标记的 markdown 行；空段落返回空串。"""
    txt = (p.text or "").strip()
    if not txt:
        return ""
    style = ""
    try:
        style = (p.style.name or "") if p.style else ""
    except Exception:  # noqa: BLE001
        style = ""
    if style.startswith(("Title",)):
        return f"# {txt}"
    if style.startswith(("Heading 1", "Heading1", "Heading 01")):
        return f"# {txt}"
    if style.startswith(("Heading 2", "Heading2", "Heading 02")):
        return f"## {txt}"
    if style.startswith(("Heading 3", "Heading3", "Heading 03")):
        return f"### {txt}"
    if style.startswith("Heading"):
        return f"#### {txt}"
    s = style.lower()
    if "list" in s or "bullet" in s or "number" in s:
        return f"- {txt}"
    return txt


def _render_table_md(tb):
    """表格 → GitHub 管道表；横向 / 纵向合并单元格均去重留空；单元格内嵌套表格递归渲染。"""
    from docx.oxml.ns import qn  # type: ignore
    rows = tb.rows
    if not rows:
        return ""
    out = []
    global_seen = set()  # 跨行去重：纵向合并的延续格（同一 _tc 再次出现）留空
    first = True
    for row in rows:
        row_seen = set()  # 行内去重：横向合并的延续格留空
        cell_md = []
        for c in row.cells:
            tc = c._tc
            if tc in row_seen:
                cell_md.append("")  # 行内横向合并延续
            else:
                row_seen.add(tc)
                if tc in global_seen:
                    cell_md.append("")  # 跨行纵向合并延续
                else:
                    global_seen.add(tc)
                    vm = tc.find(qn("w:vMerge"))
                    if vm is not None and vm.get(qn("w:val")) != "restart":
                        cell_md.append("")  # 纵向合并非起始格：留空
                    else:
                        cell_md.append(_cell_text_md(c))
        out.append("| " + " | ".join(cell_md) + " |")
        if first:
            out.append("| " + " | ".join(["---"] * len(cell_md)) + " |")
            first = False
    return "\n".join(out)


def _cell_text_md(cell):
    paras = []
    for p in cell.paragraphs:
        t = (p.text or "").strip()
        if t:
            paras.append(t)
    body = " ".join(paras)
    try:
        nested = [m for m in (_render_table_md(t) for t in cell.tables) if m]
    except Exception:  # noqa: BLE001
        nested = []
    if nested:
        body = (body + " " if body else "") + " ".join(nested)
    body = body.replace("\n", " ").replace("|", "\\|").strip()
    return body


def _sniff_mime(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


def _image_to_b64(data, max_side=1568):
    """缩放压缩后再转 base64，降低视觉模型调用成本；Pillow 不可用时用原图。"""
    import io as _io
    try:
        from PIL import Image  # type: ignore
        im = Image.open(_io.BytesIO(data))
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)))
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:
        return base64.b64encode(data).decode("ascii"), _sniff_mime(data)


OCR_PROMPT = (
    "你是 OCR 助手。请识别图片中的全部文字，并判断『清晰度』。只输出一个 JSON 对象：\n"
    '{"clear": true 或 false, "text": "识别出的全部文字", "note": "一句话说明"}\n'
    "清晰度判定标准（很重要）：\n"
    "- 只要文字整体能被正常辨认读出来（即使有轻微噪点、轻微倾斜、个别字模糊），clear 就设为 true；\n"
    "- 只有存在明显缺陷时才设为 false：严重模糊/失焦、强烈反光或阴影遮挡、关键内容被裁切、字号过小完全无法辨认。\n"
    "请以『人能否顺畅阅读』为准，不要因为轻微瑕疵就判为不清晰。\n"
    "无论清晰与否，都必须在 text 里尽力输出你能识别的全部文字，务必保留题号（1. / 一、）、选项（A. B. C. D.）和答案行（答：/答案：）。\n"
    "只输出 JSON，不要 markdown 代码块，不要输出 JSON 以外的内容；若图中确实没有文字，text 返回空字符串并在 note 说明。"
)


def _message_text(msg):
    """兼容：content 可能是字符串或分片数组；思考模型可能把正文放在 reasoning_content。"""
    c = msg.get("content", "")
    if isinstance(c, list):
        c = "".join([p.get("text", "") for p in c if isinstance(p, dict)])
    c = (c or "").strip()
    if not c:
        c = (msg.get("reasoning_content") or "").strip()
    return c


def _vision_ocr(b64, mime, api_key, base_url, model):
    # A3：忽略前端传入 Key，统一使用服务端环境变量
    api_key = LLM_API_KEY
    if not api_key:
        raise RuntimeError(
            "OCR 需要 API Key：请在服务端配置环境变量 LLM_API_KEY。"
        )
    base = ((base_url or "").strip() or LLM_BASE_URL).rstrip("/")
    model = (model or "").strip() or LLM_VISION_MODEL
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"调用视觉模型失败：{e}（请检查 API Key / Base URL / 视觉模型名是否正确，以及网络能否访问该端点）")

    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    content = _message_text(msg)
    content = _re.sub(r"^```(?:json)?", "", content).strip()
    content = _re.sub(r"```$", "", content).strip()
    try:
        obj = json.loads(content)
    except Exception:
        return {"clear": True, "text": content, "note": "模型未按 JSON 返回，已按原文提取"}
    return {
        "clear": bool(obj.get("clear", True)),
        "text": str(obj.get("text", "") or "").strip(),
        "note": str(obj.get("note", "") or "").strip(),
    }


def _process_job(jid, data, kind, api_key, base_url, ocr_model):
    """后台线程：解析 / OCR，结果写回 job。"""
    try:
        _job_update(jid, stage="parsing")
        if kind == "pdf":
            text = _extract_pdf(data)
            _job_update(jid, status="done", stage="done", text=text, note="PDF 文字提取完成")
        elif kind == "docx":
            text = _extract_docx(data)
            _job_update(jid, status="done", stage="done", text=text, note="Word 正文提取完成")
        elif kind == "image":
            _job_update(jid, stage="ocr")
            b64, mime = _image_to_b64(data)
            r = _vision_ocr(b64, mime, api_key, base_url, ocr_model)
            text = r["text"]
            warn = None
            if not text:
                warn = "没识别到文字：请确认照片对准了题目区域、光线充足，或直接把内容粘贴到文本框。"
            elif not r["clear"]:
                warn = (r["note"] or "图片不够清晰") + " —— 已完成尽力识别，请核对下方文字是否缺失。"
            _job_update(jid, status="done", stage="done", text=text, warn=warn, note=r["note"])
        else:
            text = _extract_plain(data)
            _job_update(jid, status="done", stage="done", text=text, note="文本文件读取完成")
    except Exception as e:  # noqa: BLE001
        _job_update(jid, status="error", stage="error", error=str(e))


# ---------------- 接口：上传 -> 异步任务 ----------------
def _start_doc_job(data, filename, kind, apiKey="", baseUrl="", ocrModel=""):
    """校验 + 建解析任务（单请求上传与分片合并共用）。"""
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件过大（上限 %dMB）" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
    k = _kind_of(filename or "", kind)
    if k == "doc_unsupported":
        raise HTTPException(status_code=400, detail="不支持旧版 .doc 格式，请在 Word 里另存为 .docx 后上传")
    if k == "unsupported":
        raise HTTPException(status_code=400, detail="不支持的文件类型，请上传 PDF / Word(.docx) / 文本 / 图片")
    jid = _job_new(k, filename or "")
    _EXECUTOR.submit(_process_job, jid, data, k, apiKey, baseUrl, ocrModel)
    return jid, k


@app.post("/api/doc/upload")
async def doc_upload(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form(""),
    apiKey: str = Form(""),
    baseUrl: str = Form(""),
    ocrModel: str = Form(""),
):
    require_access(request)
    data = await file.read()
    jid, k = _start_doc_job(data, file.filename, kind, apiKey, baseUrl, ocrModel)
    return {"ok": True, "job_id": jid, "kind": k, "filename": file.filename}


# ---------------- 大文件分片上传：绕过反向代理约 60MB 的单请求限制 ----------------
_CHUNK_LIMIT = 8 * 1024 * 1024          # 每片 8MB
_CHUNK_DIR = BASE_DIR / "data" / "chunks"


def _safe_uid(uid):
    if not _re.fullmatch(r"[A-Za-z0-9_-]{8,64}", uid or ""):
        raise HTTPException(status_code=400, detail="非法上传 ID")


@app.post("/api/doc/upload-chunk")
async def doc_upload_chunk(request: Request, uid: str = Form(...), idx: int = Form(...),
                           file: UploadFile = File(...)):
    require_access(request)
    _safe_uid(uid)
    if idx < 0 or idx > 2000:
        raise HTTPException(status_code=400, detail="分片序号非法")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="分片内容为空")
    if len(data) > _CHUNK_LIMIT * 2:
        raise HTTPException(status_code=413, detail="单个分片过大")
    d = _CHUNK_DIR / uid
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%05d.part" % idx)).write_bytes(data)
    # 顺手清理 24 小时前的残留分片
    try:
        now = time.time()
        for old in _CHUNK_DIR.iterdir():
            if old.is_dir() and now - old.stat().st_mtime > 86400:
                shutil.rmtree(old, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "idx": idx}


@app.post("/api/doc/upload-complete")
async def doc_upload_complete(request: Request, uid: str = Form(...), total: int = Form(...),
                              name: str = Form(""), kind: str = Form(""),
                              apiKey: str = Form(""), baseUrl: str = Form(""),
                              ocrModel: str = Form("")):
    require_access(request)
    _safe_uid(uid)
    if total <= 0 or total > 2000:
        raise HTTPException(status_code=400, detail="分片总数非法")
    d = _CHUNK_DIR / uid
    if not d.exists():
        raise HTTPException(status_code=404, detail="分片丢失，请重新上传")
    parts = sorted(d.glob("*.part"))
    if len(parts) != total:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(status_code=400,
                            detail="分片不完整（收到 %d/%d），请重新上传" % (len(parts), total))
    buf = io.BytesIO()
    for p in parts:
        buf.write(p.read_bytes())
    data = buf.getvalue()
    shutil.rmtree(d, ignore_errors=True)
    jid, k = _start_doc_job(data, name, kind, apiKey, baseUrl, ocrModel)
    return {"ok": True, "job_id": jid, "kind": k, "filename": name}


@app.get("/api/job/{job_id}")
async def job_status(job_id: str, request: Request):
    require_access(request)
    j = _job_get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if len(JOBS) > 50:
        _cleanup_jobs()
    return {
        "ok": True,
        "job_id": job_id,
        "status": j["status"],
        "stage": j["stage"],
        "kind": j["kind"],
        "filename": j["filename"],
        "text": j["text"] or "",
        "warn": j["warn"],
        "note": j["note"],
        "error": j["error"],
    }


# ==================================================================
# 服务端 LLM 能力（抽题 / 生成干扰选项 / 配置查询）
#   —— 让前端「不必携带 API Key」，Key 只保留在服务端，公网页面扒不到
# ==================================================================
def _strip_fence(s):
    s = (s or "").strip()
    s = _re.sub(r"^```(?:json)?", "", s).strip()
    s = _re.sub(r"```$", "", s).strip()
    return s


def _parse_json_array(s):
    s = _strip_fence(s)
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    try:
        i, j = s.index("["), s.rindex("]")
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def _parse_json_object(s):
    s = _strip_fence(s)
    try:
        v = json.loads(s)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    try:
        i, j = s.index("{"), s.rindex("}")
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def _chat_text(user_text, system=None, api_key="", base_url="", model="",
               temperature=0.2, max_tokens=4000, timeout=120):
    # A3：忽略前端传入 Key，统一使用服务端环境变量
    key = LLM_API_KEY
    if not key:
        raise RuntimeError("调用大模型需要 API Key：请在服务端配置环境变量 LLM_API_KEY。")
    base = ((base_url or "").strip() or LLM_BASE_URL).rstrip("/")
    model = (model or "").strip() or LLM_TEXT_MODEL
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user_text})
    payload = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": msgs}
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"调用模型失败：{e}（请检查 API Key / Base URL / 模型名与网络）")
    return _message_text(((data.get("choices") or [{}])[0].get("message") or {}))


def _chat_messages(msgs, api_key="", base_url="", model="", temperature=0.2, max_tokens=700, timeout=120):
    """多轮对话核心：接受完整 messages 历史（供 AI 客服使用）。"""
    # A3：忽略前端传入 Key，统一使用服务端环境变量
    key = LLM_API_KEY
    if not key:
        raise RuntimeError("缺少 API Key（服务端未配置 LLM_API_KEY）")
    base = ((base_url or "").strip() or LLM_BASE_URL).rstrip("/")
    model = (model or "").strip() or LLM_TEXT_MODEL
    payload = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": msgs}
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"调用模型失败：{e}（请检查 API Key / Base URL / 模型名与网络）")
    return _message_text(((data.get("choices") or [{}])[0].get("message") or {}))


class ExtractReq(BaseModel):
    text: str
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""
    # 按版块抽题：前端传入 [{"title":"2.1 光合作用","text":"..."}]
    # 为空则退化为「整篇一次性抽题」（旧行为，完全兼容）
    sections: list = []
    maxPerSection: int = 0   # 每个版块最多出几题，0 = 不限
    # 题型：auto=模型自定 / single=单选 / multi=多选 / judge=判断 / essay=简答 / mix=混合
    qtype: str = "auto"


class OptionsReq(BaseModel):
    items: list = []
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""


class RegenReq(BaseModel):
    question: str
    answer: str = ""          # 用户不满意的旧答案（可空）
    context: str = ""         # 文档上下文片段
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""


class CleanReq(BaseModel):
    text: str
    apiKey: str = ""
    baseUrl: str = ""
    model: str = ""


@app.get("/api/llm-config")
async def llm_config():
    """前端读取默认端点/模型；只回传「是否已配置服务端 Key」，绝不回传 Key 本身。"""
    return {
        "base_url": LLM_BASE_URL,
        "text_model": LLM_TEXT_MODEL,
        "vision_model": LLM_VISION_MODEL,
        "server_key": bool(LLM_API_KEY),
        "access_required": bool(ACCESS_CODE),
    }


# ---------------- AI 客服「小暹」（暹罗猫） ----------------
ASSISTANT_SYSTEM = """你是「问题自检平台」（企业问题自检中心）的专属 AI 助手，名字叫小暹，形象是一只聪明活泼的 3D 暹罗猫（蓝眼睛、奶油色身体、深棕色耳朵尾巴）。

【身份边界（最重要）】
- 你只属于「问题自检平台」这一个大项目，只深入了解本平台的功能与用法。
- 用户问到其他项目/其他系统（例如合同审查、法律咨询、电商客服、问数工具等）时，
  要明确说明：你是问题自检平台的助手，只熟悉本平台，那些问题帮不上忙；
  然后自然地把话题拉回本平台（例如推荐试试新建自检任务或看看风险报告）。
- 不了解的本平台细节不要编造，引导用户在界面上实际操作试试。

【本平台知识（回答依据）】
1. 平台定位：企业问题自检中心——把企业里的合规/流程/技术/数据/权限问题整理成任务来检查、
   诊断分类、跟踪整改直到闭环，最后汇总成风险报告。
2. 五个页面：
   ① 首页仪表盘：自检完成率/待检问题/高风险项/已完成任务四张统计卡、今日自检任务、
      高频问题分类环图（点分类列表可联动高亮）、底部「快速自检」表单（确认范围+选类别，
      一键生成任务或存草稿）；
   ② 自检任务：新建任务（名称/类别/截止时间/范围说明），状态流转 待开始→进行中→已完成，
      可删除；
   ③ 问题库：五类问题（合规/流程/技术/数据/权限）、风险等级 高/中/低，
      整改流转 待整改→整改中→已闭环；可新增、删除、点「导入示例数据」补充演示数据；
   ④ 风险报告：问题总数/高风险未闭环/整改闭环率、各类别分布条形图、高风险清单，
      支持导出 Markdown 和打印；
   ⑤ 系统设置：导出 JSON 备份、重置演示数据、清空全部数据。
3. 智能工具「文档抽题自检」：侧边栏进入，上传 PDF/Word/TXT/MD/图片或粘贴文本，
   自动抽取自测题逐题翻卡自检、汇总薄弱点，还有间隔重复复习。
4. 登录方式：账号密码、手机号验证码、微信扫码、支付宝扫码，也可以游客一键体验；
   游客数据在注册登录后会自动并入正式账号。
5. 数据说明：任务与问题保存在浏览器本地（localStorage），仅当前浏览器可见；
   正式部署可接后端持久化。
6. 限制：访问门槛默认关闭（自部署模式）；单文件上传 ≤200MB；旧版 .doc 需另存为 .docx。

【风格】
- 简体中文，亲切自然，偶尔带一点"喵~"的口癖（不要每句都带）。
- 回答简明，重点突出；讲步骤时用 ①②③ 编号，控制在 200 字以内。

【兜底（很重要）】
用户聊天气、吃饭、新闻等与项目无关的日常闲聊时，友好接住：给简短有用的回应，不要拒绝；
回应完自然把话题拉回本平台（例如"对了喵，要不要建一个自检任务试试？"）。
涉及危险、违法或你不确定的专业问题，提醒用户咨询官方/专业人士。"""


class AssistantReq(BaseModel):
    message: str
    history: list = []


@app.post("/api/assistant/chat")
async def assistant_chat(req: AssistantReq, request: Request):
    require_access(request)
    _llm_quota_check(request)
    msg = (req.message or "").strip()[:2000]
    if not msg:
        raise HTTPException(status_code=400, detail="消息为空")
    hist = []
    for h in (req.history or [])[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            c = h["content"].strip()[:1500]
            if c:
                hist.append({"role": h["role"], "content": c})
    msgs = [{"role": "system", "content": ASSISTANT_SYSTEM}] + hist + [{"role": "user", "content": msg}]
    try:
        reply = _chat_messages(msgs, temperature=0.6, max_tokens=700)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="客服大脑暂时连不上：" + str(e))
    return {"ok": True, "reply": reply}


# ---------------- 智能搜索（联网查百度 + AI 总结） ----------------
class SearchReq(BaseModel):
    q: str
    summarize: bool = True


_SEARCH_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _strip_tags(s):
    return _re.sub(r"<[^>]+>", "", s or "").replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').strip()


def _baidu_search(q, n=10):
    """抓取百度搜索结果：按结果容器逐条解析（标题/链接/摘要天然对齐）。"""
    url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(q) + "&rn=%d&ie=utf-8" % n
    req = urllib.request.Request(url, headers=_SEARCH_UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode("utf-8", "ignore")
    results = []
    # 每条结果都包在 <div class="result c-container ..."> 容器里，逐块解析
    for chunk in _re.split(r'<div[^>]*class="[^"]*result[^"]*c-container[^"]*"', html)[1:]:
        tm = _re.search(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, _re.S)
        if not tm:
            continue
        u, t = tm.group(1), _strip_tags(tm.group(2))
        if u.startswith("/"):
            u = "https://www.baidu.com" + u
        if not (t and u.startswith("http")):
            continue
        sm = _re.search(r'class="[^"]*(?:c-abstract|content-right|c-span9)[^"]*"[^>]*>(.*?)</div>', chunk, _re.S)
        snippet = _strip_tags(sm.group(1))[:300] if sm else ""
        results.append({"title": t, "url": u, "snippet": snippet})
        if len(results) >= n:
            break
    # 兜底：容器结构变了时退回整页 h3 扫描
    if not results:
        for m in _re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, _re.S):
            u, t = m.group(1), _strip_tags(m.group(2))
            if u.startswith("/"):
                u = "https://www.baidu.com" + u
            if t and u.startswith("http"):
                results.append({"title": t, "url": u, "snippet": ""})
                if len(results) >= n:
                    break
    # 再兜底：连 h3 都没有（可能是验证页）
    if not results:
        title = _re.search(r"<title>(.*?)</title>", html, _re.S)
        results.append({"title": "百度返回异常页" + ("（" + _strip_tags(title.group(1))[:60] + "）" if title else ""),
                        "url": "https://www.baidu.com/s?wd=" + urllib.parse.quote(q), "snippet": ""})
    return results


@app.post("/api/websearch")
async def api_websearch(req: SearchReq, request: Request):
    require_access(request)
    _llm_quota_check(request)
    q = (req.q or "").strip()[:120]
    if not q:
        raise HTTPException(status_code=400, detail="搜索词为空")
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, _baidu_search, q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="联网搜索失败（网络或百度限制）：" + str(e))
    summary = ""
    if req.summarize:
        ctx = "\n".join(
            "[%d] %s：%s" % (i + 1, r["title"], r["snippet"] or "（无摘要）")
            for i, r in enumerate(results[:8]))
        sys_p = ("你是「文档自检」应用的联网搜索助手。根据下面的百度搜索结果，用简体中文给用户一个简明、"
                 "准确的回答（150 字以内，末尾用〔参考1〕〔参考3〕这样的格式注明引用了哪几条）。"
                 "如果结果与问题无关，就如实说没查到有效信息。")
        try:
            summary = await loop.run_in_executor(
                None,
                lambda: _chat_messages(
                    [{"role": "system", "content": sys_p},
                     {"role": "user", "content": "问题：" + q + "\n\n搜索结果：\n" + (ctx or "（无结果）")}],
                    temperature=0.3, max_tokens=500, timeout=60))
        except Exception:  # noqa: BLE001
            summary = ""   # 总结失败不影响结果展示
    return {"ok": True, "q": q, "results": results, "summary": summary}


_QTYPE_RULES = {
    "auto":  ("题型按内容选最合适的形式：对比、原因、流程、解释类知识点（如「X和Y有什么区别」「为什么…」「简述…」）一律出**简答题**；"
              "陈述句能判对错的出**判断题**；答案能列出2个及以上并列正确项的列举类才出**多选**；"
              "其余有唯一明确答案、能构造3个可靠干扰项的出**单选**。"),
    "single": "全部出**单选题**：每题恰好 4 个选项，有且只有 1 个正确答案。",
    "multi": "全部出**多选题**：每题 4-5 个选项，正确答案必须有 2 个或以上（correct 填多个字母，如 \"A,C\"）。若某题实在凑不出 2 个可靠正确项，就把该题降级为单选或简答。",
    "judge": "全部出**判断题**：每题 options 固定为 [\"正确\", \"错误\"]，正确项用 correct 标记（\"A\"=正确，\"B\"=错误）。",
    "essay": "全部出**简答题**：options 用空数组 []，correct 填空字符串，answer 给出简明完整的参考答案。",
    "mix":   ("混合出题，但题型必须匹配内容，不要为凑数量硬配："
              "对比、原因、流程、解释类知识点（如「X和Y有什么区别」「为什么…」「简述…」）一律出**简答题**；"
              "陈述句能判对错的出**判断题**；答案能列出≥2个并列正确项的列举类出**多选**（correct 填如 \"A,C\"）；"
              "其余有唯一明确答案的出**单选**。四种题型都要有。"),
}


def _qtype_rule(qt):
    return _QTYPE_RULES.get((qt or "auto").strip(), _QTYPE_RULES["auto"])


_SYS_EXTRACT = (
    "你是一个出题助手。用户会给你一段文档，请把其中的知识点拆成独立的\"自测题\"。"
    "只输出 JSON 数组："
    "[{\"question\":\"题目\",\"answer\":\"参考答案（简洁）\",\"topic\":\"该题考查的知识点或方面（6字以内）\","
    "\"qtype\":\"single|multi|judge|essay 之一\","
    "\"options\":[\"选项A内容\",\"选项B内容\",\"选项C内容\",\"选项D内容\"],\"correct\":\"A\"}]。"
    "qtype 取值：single=单选、multi=多选、judge=判断、essay=简答。"
    "correct 用选项字母标记正确项：单选/判断填一个字母（如 \"A\"）；多选填多个字母（如 \"A,C\"）；简答题 correct 填 \"\" 且 options 用 []。"
    "answer 始终给出正确内容的文字版（简答题就是参考答案本身）。"
    "【逐块对应】输入文本按空行分为若干题目块；请逐块出题，第 N 道题的题干与答案只能来自第 N 块，"
    "不得把后面块的内容并入前面题的答案。"
    "【答案纯净性硬规则】每道题的 answer 只允许包含该题自己的答案内容；"
    "如果原文里答案后面紧跟了下一个问题（如「…有哪些应用场景？」「…怎么避免？」）或其他题目的文字，必须在那里停住，"
    "把后续内容拆成独立的新题，绝不并入当前题的 answer。answer 末尾不得残留下一题的题干或半截话。"
    "topic 用来做「薄弱方面」统计，请写得具体且同类题保持一致（如「光合作用」「傅里叶变换」「合同法」）。"
    "【无关内容过滤】只根据学习相关的知识点出题；原文里的闲聊、日程安排、寒暄、广告、页码等与知识无关的内容"
    "（如「下午三点开会」「晚饭吃什么」）一律忽略，绝不为它们生成题目；"
    "也不要出「这份资料讲什么」「本批次最多几道题」这类针对出题任务本身的元问题。"
    "题目必须考查可被事实判定的具体知识点；观点、感想、励志、口号类文字（如「坚持就会有收获」「明天会更好」）不是知识，绝不出题。"
    "题干必须独立成立，禁止以「根据本版块」「根据原文」「根据材料」「根据上文」开头。"
    "不要输出任何解释、不要使用 markdown 代码块，只返回合法 JSON 数组。"
)

_SYS_SECTION = (
    "你是一个出题助手。用户会给你文档中的**一个版块**（给出版块标题与该版块正文）。"
    "请只围绕这个版块的内容出题，不要引用版块以外的知识，也不要重复版块标题本身作为问题。"
    "把该版块的知识点拆成独立的\"自测题\"，只输出 JSON 数组："
    "[{\"question\":\"题目\",\"answer\":\"参考答案（简洁）\",\"topic\":\"该题考查的知识点或方面（6字以内）\","
    "\"qtype\":\"single|multi|judge|essay 之一\","
    "\"options\":[\"选项A内容\",\"选项B内容\",\"选项C内容\",\"选项D内容\"],\"correct\":\"A\"}]。"
    "qtype 取值：single=单选、multi=多选、judge=判断、essay=简答。"
    "correct 用选项字母标记正确项：单选/判断填一个字母（如 \"A\"）；多选填多个字母（如 \"A,C\"）；简答题 correct 填 \"\" 且 options 用 []。"
    "answer 始终给出正确内容的文字版（简答题就是参考答案本身）。"
    "【逐块对应】输入文本按空行分为若干题目块；请逐块出题，第 N 道题的题干与答案只能来自第 N 块，"
    "不得把后面块的内容并入前面题的答案。"
    "【答案纯净性硬规则】每道题的 answer 只允许包含该题自己的答案内容；"
    "如果原文里答案后面紧跟了下一个问题（如「…有哪些应用场景？」「…怎么避免？」）或其他题目的文字，必须在那里停住，"
    "把后续内容拆成独立的新题，绝不并入当前题的 answer。answer 末尾不得残留下一题的题干或半截话。"
    "【无关内容过滤】只根据本版块学习相关的知识点出题；闲聊、日程、寒暄、广告等与知识无关的内容一律忽略，绝不为它们生成题目。题目必须考查可被事实判定的具体知识点；观点、感想、励志、口号类文字不是知识，绝不出题。题干必须独立成立，禁止以「根据本版块」「根据原文」开头。"
    "不要输出任何解释、不要使用 markdown 代码块，只返回合法 JSON 数组。"
)


def _norm_sections(raw):
    """归一化前端传来的版块列表，去掉空块。"""
    out = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        if not isinstance(x, dict):
            continue
        t = str(x.get("text") or "").strip()
        if not t:
            continue
        out.append({"title": str(x.get("title") or "").strip(), "text": t})
    return out


_Q_STEM_RE = _re.compile(r'(什么是|什么区别|区别是什么|有哪些|哪些是|为什么|怎么办|怎么避免|怎样避免|如何解决|请简述|请举例|举例说明|简述其|可能导致|什么问题)')


_VALID_END = _re.compile(r'[。！？!?；;）)”"]$')


def _clean_answer(ans):
    """答案净化：把模型混进 answer 里的下一题内容截掉，并保证以完整句结尾。
    特征：中后段出现以问号结尾的短句，或以疑问词开头的短句 —— 那是别的题的题干。"""
    a = (ans or '').strip()
    if not a:
        return a
    if '？' in a or '?' in a:
        parts = _re.split(r'(?<=[。！？!?\n])', a)
        out = []
        broke = False
        for seg in parts:
            s = seg.strip()
            if not s:
                continue
            if out:
                is_q = s.endswith('？') or s.endswith('?')
                stem = bool(_Q_STEM_RE.search(s[:24]))
                if (is_q and len(s) <= 50) or (stem and len(s) <= 30):
                    broke = True
                    break
            out.append(seg)
        # 截断后清除残留的"小节标题"短尾巴（如「闭包相关」：无标点、≤20字）
        if broke:
            while len(out) > 1:
                last = out[-1].strip()
                if last and len(last) <= 20 and not _VALID_END.search(last):
                    out.pop()
                else:
                    break
        trimmed = ''.join(out).strip()
    else:
        trimmed = a
    # 通用规则：答案必须以完整句结尾 —— 结尾"无标点短尾巴"（节标题残留）裁掉
    if trimmed and not _VALID_END.search(trimmed):
        m = _re.search(r'^(.*[。！？!?；;])', trimmed, _re.S)
        if m and len(m.group(1).strip()) >= 10:
            trimmed = m.group(1).strip()
    return trimmed if len(trimmed) >= 10 else a


_QNUM_RE = _re.compile(r'^\s*(?:\(?[\d]+\)\b|[\d]+[\)\.、]|[一二三四五六七八九十]+[、\.]|（[一二三四五六七八九十\d]+）|第\s*[\d一二三四五六七八九十]+\s*题[：:.]?|Q\d*[:：]?)')
_NORM_RE = _re.compile(r'[\s\*#（）()「」【】，。；：、？！?!·"“”\']+')


def _is_node_boundary(line):
    s = line.strip()
    if _re.match(r'^\*\*\S', s):
        return True
    if _QNUM_RE.match(s) and len(s) <= 80 and not _re.match(r'^[A-Da-d][\.、]', s):
        return True
    if s.endswith('？') and len(s) <= 60 and not _re.match(r'^(答|解析)', s):
        return True
    return False


def _node_spans(text):
    """按最小节点切分源文本，返回每个节点在原文中的 (start, end) 偏移（与前端 splitNodes 同规则）。"""
    spans = []
    off = 0
    start = 0
    have = False
    for line in text.split('\n'):
        if have and _is_node_boundary(line):
            spans.append((start, off))
            start = off
        have = True
        off += len(line) + 1
    spans.append((start, off))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _clip_answers_to_nodes(items, src):
    """0 误差兜底：每道题的答案强制裁剪回它自己的知识点节点内。
    1) 用题干在源文本中找最匹配的节点；2) 在该节点内定位答案起点；
    3) 答案与文档原文逐字比对，偏差处即截断 —— 答案只能是该节点文档原话的前缀，绝不越界。"""
    if not src or not items:
        return items
    import difflib
    spans = _node_spans(src)
    if not spans:
        return items

    def norm(s):
        return _NORM_RE.sub('', s or '')

    nodes_norm = [norm(src[a:b])[:120] for a, b in spans]
    for it in items:
        ans = (it.get('answer') or '').strip()
        qn = norm(it.get('question') or '')[:60]
        if not ans or not qn:
            continue
        best_r, best_span = 0.0, None
        for (a, b), nn in zip(spans, nodes_norm):
            r = difflib.SequenceMatcher(None, qn, nn).ratio()
            if r > best_r:
                best_r, best_span = r, (a, b)
        if best_span is None or best_r < 0.30:
            it['answer'] = _clean_answer(ans)
            continue
        a, b = best_span
        p = src.find(ans[:12], a, b)
        if p < 0:
            p = src.find(ans[:8], a, b)
        if p >= 0 and p < b:
            seg = src[p:b]
            n = 0
            while n < len(ans) and n < len(seg) and ans[n] == seg[n]:
                n += 1
            if n >= 10:
                cand = seg[:n]
                m = _re.search(r'^(.*[。！？!?；;])', cand, _re.S)
                cand = (m.group(1) if m else cand).strip()
                # 尾部"无标点短尾巴"（节标题残留）裁掉，答案必须以完整句结尾
                if cand and not _VALID_END.search(cand):
                    m2 = _re.search(r'^(.*[。！？!?；;])', cand, _re.S)
                    if m2 and len(m2.group(1).strip()) >= 10:
                        cand = m2.group(1).strip()
                it['answer'] = cand
            else:
                it['answer'] = _clean_answer(ans)
        else:
            it['answer'] = _clean_answer(ans)
    return items


_META_Q = __import__("re").compile(r"^根据(本版块|原文|材料|上文|资料|文中)")


def _qa_from_model_arr(arr, default_section=""):
    """把模型返回的 JSON 数组清洗成统一结构，并做题型兜底与答案净化。"""
    items = []
    for x in arr or []:
        if not isinstance(x, dict) or not x.get("question"):
            continue
        if _META_Q.match(str(x["question"]).strip()):
            continue  # 「根据本版块…」类元问题直接丢弃
        opts = x.get("options")
        q = {
            "question": str(x["question"]).strip(),
            "answer": _clean_answer(str(x.get("answer") or "").strip()),
            "topic": str(x.get("topic") or "").strip(),
            "section": str(x.get("section") or default_section).strip(),
            "qtype": str(x.get("qtype") or "").strip().lower(),
            "correct": str(x.get("correct") or "").strip().upper(),
            "options": [str(o) for o in opts] if isinstance(opts, list) else [],
        }
        # ---- 题型兜底：保证「题型-答案」自洽，避免多选凑不出正确项 ----
        letters = [c for c in q["correct"] if c in "ABCDE"]
        if q["qtype"] == "multi" and len(letters) < 2:
            # 多选必须 ≥2 个正确项；不足就降级：1 个字母且有选项 → 单选，否则 → 简答
            if len(letters) == 1 and len(q["options"]) >= 2:
                q["qtype"] = "single"
                q["correct"] = letters[0]
            else:
                q["qtype"] = "essay"
                q["options"], q["correct"] = [], ""
        elif q["qtype"] == "single" and len(q["options"]) < 2:
            q["qtype"] = "essay"     # 单选却没有可用选项 → 简答
            q["options"], q["correct"] = [], ""
        elif q["qtype"] == "essay":
            q["options"], q["correct"] = [], ""
        if q["qtype"] not in ("single", "multi", "judge", "essay"):
            q["qtype"] = "essay" if not q["options"] else "single"
        items.append(q)
    return items


@app.post("/api/extract")
async def api_extract(req: ExtractReq, request: Request):
    require_access(request)
    _llm_quota_check(request)
    limit = max(0, int(req.maxPerSection or 0))

    # ---- 按版块分别出题：每个版块一次独立的大模型调用，并行执行 ----
    secs = _norm_sections(req.sections)
    # 过短版块（如只有文档标题/目录行）不值得出题，直接跳过，防止模型编出「资料主题」类元问题
    secs = [s for s in secs if len((s.get("text") or "").strip()) >= 40]
    if secs:
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(4)  # 限并发，避免把模型打爆

        def kwargs():
            return dict(api_key=req.apiKey, base_url=req.baseUrl,
                        model=req.model, temperature=0.2, max_tokens=2500)

        async def one(i, sec):
            head = ("版块标题：" + sec["title"] + "\n\n") if sec["title"] else ""
            tail = ("\n\n（本版块请最多产出 %d 道题）" % limit) if limit else ""
            payload = head + sec["text"] + tail
            sys_p = _SYS_SECTION + "\n题型要求：" + _qtype_rule(req.qtype)
            try:
                async with sem:
                    raw = await loop.run_in_executor(
                        None, lambda: _chat_text(payload, system=sys_p, **kwargs()))
            except Exception as e:  # noqa: BLE001
                return i, [], str(e)
            arr = _parse_json_array(raw)
            if arr is None:
                return i, [], "模型返回无法解析为题目数组"
            its = _clip_answers_to_nodes(_qa_from_model_arr(arr, default_section=sec["title"]), sec["text"])
            if limit:
                its = its[:limit]
            return i, its, ""

        gathered = await asyncio.gather(*[one(i, s) for i, s in enumerate(secs)]) \
            if len(secs) > 1 else [await one(0, secs[0])]

        out, failed = [], []
        for i, its, err in sorted(gathered, key=lambda x: x[0]):
            if err:
                failed.append({"title": secs[i]["title"] or ("版块%d" % (i + 1)), "error": err})
            out.extend(its)
        return {"ok": True, "questions": out, "failed": failed, "sections": len(secs)}

    # ---- 旧行为：整篇一次性抽题 ----
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")
    sys_p = _SYS_EXTRACT + "\n题型要求：" + _qtype_rule(req.qtype)
    try:
        raw = _chat_text(text, system=sys_p, api_key=req.apiKey, base_url=req.baseUrl,
                         model=req.model, temperature=0.2, max_tokens=4000)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    arr = _parse_json_array(raw)
    if arr is None:
        raise HTTPException(status_code=502, detail="模型返回无法解析为题目数组")
    out = _clip_answers_to_nodes(_qa_from_model_arr(arr), text)
    return {"ok": True, "questions": out}


@app.post("/api/asr")
async def api_asr(request: Request, file: UploadFile = File(...),
                  apiKey: str = Form(""), baseUrl: str = Form("")):
    """语音转文字：接收浏览器录好的 WAV，转发给 SiliconFlow 的 SenseVoice ASR。"""
    require_access(request)
    _llm_quota_check(request)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="没有收到音频")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="音频太大（>15MB），请说短一点")
    # A3：忽略前端传入 Key，统一使用服务端环境变量
    key = LLM_API_KEY
    if not key:
        raise HTTPException(status_code=400,
                            detail="语音识别需要 API Key：请在服务端配置环境变量 LLM_API_KEY。")
    base = ((baseUrl or "").strip() or LLM_BASE_URL).rstrip("/")
    model = os.getenv("ASR_MODEL") or "FunAudioLLM/SenseVoiceSmall"
    boundary = "----wbasr" + uuid.uuid4().hex
    buf = io.BytesIO()
    buf.write(("--%s\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n%s\r\n"
               % (boundary, model)).encode())
    buf.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"answer.wav\"\r\n"
               "Content-Type: audio/wav\r\n\r\n" % boundary).encode())
    buf.write(data)
    buf.write(("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(
        base + "/audio/transcriptions", data=buf.getvalue(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        def _call():
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(None, _call)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="语音识别服务调用失败：" + str(e))
    text = str(out.get("text") or "").strip()
    return {"ok": True, "text": text}


@app.post("/api/regen-answer")
async def api_regen_answer(req: RegenReq, request: Request):
    """用户对某题的参考答案不满意时，结合文档上下文重新生成答案。"""
    require_access(request)
    _llm_quota_check(request)
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="缺少题目")
    ctx = (req.context or "").strip()
    if len(ctx) > 6000:
        ctx = ctx[:6000]
    sys_p = (
        "你是出题助手。根据给出的文档上下文，为这道自测题重新生成一个简洁、准确、完整的参考答案。"
        "优先依据上下文；上下文不足以回答时，可用通用知识补全，但不要编造文档中不存在的结论。"
        "【答案纯净性】只回答题目问的内容；如果上下文里这道答案后面跟着别的问题（如应用场景、如何避免等），"
        "那些属于别的题，不要并入本答案。answer 末尾不得残留下一题的题干或半截话。"
        "只输出答案文本本身（2-4 句话），不要任何前缀、解释或 markdown 代码块。"
    )
    user = "文档上下文：\n" + (ctx or "（无）") + "\n\n题目：" + q
    if (req.answer or "").strip():
        user += "\n\n当前答案（用户不满意，请换一个角度或更清晰地作答）：\n" + req.answer.strip()
    try:
        raw = _chat_text(user, system=sys_p, api_key=req.apiKey, base_url=req.baseUrl,
                         model=req.model, temperature=0.4, max_tokens=600)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    ans = _strip_fence(str(raw or "")).strip()
    if not ans:
        raise HTTPException(status_code=502, detail="模型没有返回答案，请重试")
    return {"ok": True, "answer": ans}


_CLEAN_SYSTEM = (
    "你是文档整理助手。把用户给出的原始识别文本整理成规范、清晰的「知识题库 / 试卷」格式。"
    "要求："
    "1) 只保留与知识点、题目、答案相关的内容；删除试卷说明、考生须知、得分栏、装订线、"
    "残留的页码页眉、以及一切与知识内容无关的信息；"
    "2) 用 Markdown 组织：章节用 # / ## 标题；题目用 1. 2. 3. 连续编号；答案紧跟题目下一行用「答：」开头；"
    "选择题选项用 A. B. C. D. 各占一行；表格用管道表；"
    "3) 保留全部知识点：不改写、不遗漏、不新增原文没有的内容；"
    "4) 只输出整理后的 Markdown 正文，不要任何解释或前后缀。"
)

_CLEAN_MAX_INPUT = 20000     # 超长文本截断（整理是逐字重排，太长会超输出上限）


@app.post("/api/clean-text")
async def api_clean_text(req: CleanReq, request: Request):
    """把识别出的正文交给大模型整理成清晰的题库/试卷格式。"""
    require_access(request)
    _llm_quota_check(request)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")
    truncated = False
    if len(text) > _CLEAN_MAX_INPUT:
        text = text[:_CLEAN_MAX_INPUT]
        truncated = True
    try:
        raw = _chat_text(text, system=_CLEAN_SYSTEM, api_key=req.apiKey, base_url=req.baseUrl,
                         model=req.model, temperature=0.1, max_tokens=8000)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    cleaned = _strip_fence(str(raw or "")).strip()
    if not cleaned:
        raise HTTPException(status_code=502, detail="模型没有返回内容，请重试")
    if truncated:
        cleaned += "\n\n（原文较长，本次仅整理了前 %d 字）" % _CLEAN_MAX_INPUT
    return {"ok": True, "text": cleaned}


# ---------------- 异步出题任务：大文档多版块出题耗时 > 网关 60s，改为提交+轮询 ----------------
EXTRACT_JOBS = {}                       # jid -> {status, stage, questions, failed, total, done, created, error}
_EXTRACT_JOB_LOCK = threading.Lock()


def _cleanup_extract_jobs():
    now = time.time()
    with _EXTRACT_JOB_LOCK:
        for k in [k for k, v in EXTRACT_JOBS.items() if now - v["created"] > 1800]:
            EXTRACT_JOBS.pop(k, None)


@app.post("/api/extract-job")
async def extract_job_start(req: ExtractReq, request: Request):
    require_access(request)
    _llm_quota_check(request)
    _cleanup_extract_jobs()
    jid = uuid.uuid4().hex
    with _EXTRACT_JOB_LOCK:
        EXTRACT_JOBS[jid] = {"status": "processing", "stage": "排队中", "questions": [],
                             "failed": [], "total": 0, "done": 0, "created": time.time(), "error": ""}
    threading.Thread(target=_run_extract_job, args=(jid, req.model_dump()), daemon=True).start()
    return {"ok": True, "job_id": jid}


def _run_extract_job(jid, payload):
    try:
        secs = _norm_sections(payload.get("sections"))
        limit = max(0, int(payload.get("maxPerSection") or 0))
        qt = payload.get("qtype") or "auto"
        kwargs = dict(api_key=payload.get("apiKey") or "", base_url=payload.get("baseUrl") or "",
                      model=payload.get("model") or "", temperature=0.2, max_tokens=2500)
        if secs:
            with _EXTRACT_JOB_LOCK:
                EXTRACT_JOBS[jid]["total"] = len(secs)

            def _one(i, sec):
                head = ("版块标题：" + sec["title"] + "\n\n") if sec["title"] else ""
                tail = ("\n\n（本批次请最多产出 %d 道题）" % limit) if limit else ""
                with _EXTRACT_JOB_LOCK:
                    EXTRACT_JOBS[jid]["stage"] = "第 %d/%d 批出题中：%s" % (
                        i + 1, len(secs), (sec["title"] or "未命名")[:20])
                raw = _chat_text(head + sec["text"] + tail,
                                 system=_SYS_SECTION + "\n题型要求：" + _qtype_rule(qt), **kwargs)
                arr = _parse_json_array(raw)
                if arr is None:
                    raise RuntimeError("模型返回无法解析为题目数组")
                its = _clip_answers_to_nodes(_qa_from_model_arr(arr, default_section=sec["title"]), sec["text"])
                if limit:
                    its = its[:limit]
                return its

            futures = []
            with _cf.ThreadPoolExecutor(max_workers=4) as ex:
                futures = [(i, ex.submit(_one, i, s)) for i, s in enumerate(secs)]
                for i, fut in futures:
                    try:
                        got = fut.result()
                        with _EXTRACT_JOB_LOCK:
                            EXTRACT_JOBS[jid]["questions"].extend(got)
                    except Exception as e:  # noqa: BLE001
                        with _EXTRACT_JOB_LOCK:
                            EXTRACT_JOBS[jid]["failed"].append(
                                {"title": secs[i]["title"] or ("批次%d" % (i + 1)), "error": str(e)})
                    finally:
                        with _EXTRACT_JOB_LOCK:
                            EXTRACT_JOBS[jid]["done"] += 1
            with _EXTRACT_JOB_LOCK:
                EXTRACT_JOBS[jid].update(status="done", stage="完成")
        else:
            text = (payload.get("text") or "").strip()
            if not text:
                raise RuntimeError("文本为空")
            with _EXTRACT_JOB_LOCK:
                EXTRACT_JOBS[jid]["stage"] = "整篇出题中…"
            raw = _chat_text(text, system=_SYS_EXTRACT + "\n题型要求：" + _qtype_rule(qt),
                             **dict(kwargs, max_tokens=4000))
            arr = _parse_json_array(raw)
            if arr is None:
                raise RuntimeError("模型返回无法解析为题目数组")
            with _EXTRACT_JOB_LOCK:
                EXTRACT_JOBS[jid].update(status="done", stage="完成",
                                         questions=_clip_answers_to_nodes(_qa_from_model_arr(arr), text))
    except Exception as e:  # noqa: BLE001
        with _EXTRACT_JOB_LOCK:
            EXTRACT_JOBS[jid].update(status="error", stage="失败", error=str(e))


@app.get("/api/extract-job/{jid}")
async def extract_job_status(jid: str, request: Request):
    require_access(request)
    with _EXTRACT_JOB_LOCK:
        j = EXTRACT_JOBS.get(jid)
        if not j:
            raise HTTPException(status_code=404, detail="出题任务不存在或已过期")
        return {"ok": True, "status": j["status"], "stage": j["stage"],
                "total": j["total"], "done": j["done"], "failed": j["failed"],
                "questions": j["questions"] if j["status"] == "done" else [],
                "error": j["error"]}


@app.post("/api/options")
async def api_options(req: OptionsReq, request: Request):
    require_access(request)
    _llm_quota_check(request)
    items = req.items or []
    if not items:
        raise HTTPException(status_code=400, detail="缺少题目")
    sys_p = (
        "你是出题专家。对每道题，生成 3 个「看起来合理但实际错误」的干扰选项"
        "（中文、简洁、不要与正确答案重复或同义）。"
        "只输出 JSON 对象，键为题目序号数字，值为干扰项数组，例如：{\"0\":[\"a\",\"b\",\"c\"],\"2\":[...]}。"
        "不要输出解释，不要使用 markdown 代码块。"
    )
    lines = []
    for it in items:
        lines.append(f"[{it.get('i')}] 题目：{it.get('question','')}\n正确答案：{it.get('answer','')}")
    try:
        raw = _chat_text("\n".join(lines), system=sys_p, api_key=req.apiKey, base_url=req.baseUrl,
                         model=req.model, temperature=0.4, max_tokens=2000)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    obj = _parse_json_object(raw)
    if obj is None:
        raise HTTPException(status_code=502, detail="模型返回无法解析为选项对象")
    return {"ok": True, "options": obj}


# ==================================================================
# 历史记录（需登录；按用户隔离，只能看到自己的）
#   docs     : 用户上传的每个文档 + 抽取出的题目/答案
#   sessions : 每次做题的总结（刷完 或 中途结束）
# ==================================================================
def _require_user(request: Request):
    user = get_user_from_header(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录后再使用历史记录")
    return user


class DocHistoryReq(BaseModel):
    filename: str = ""
    kind: str = ""
    text: str = ""
    questions: list = []


class SessionReq(BaseModel):
    doc_label: str = ""
    total: int = 0
    answered: int = 0
    ok: int = 0
    bad: int = 0
    choice_ok: int = 0
    choice_bad: int = 0
    score: float = 0
    max_score: float = 0
    finished: bool = False
    aspects: list = []
    detail: list = []


@app.post("/api/history/doc")
async def history_add_doc(req: DocHistoryReq, request: Request):
    """保存「一次上传的文档 + 抽出的题目答案」。"""
    user = _require_user(request)
    text = (req.text or "").strip()
    if not text and not req.questions:
        raise HTTPException(status_code=400, detail="没有可保存的内容")
    rid = db.add_history_doc(
        user["id"],
        (req.filename or "未命名文档").strip()[:200],
        (req.kind or "text").strip()[:20],
        text,
        json.dumps(req.questions or [], ensure_ascii=False),
    )
    return {"ok": True, "id": rid}


@app.get("/api/history/docs")
async def history_docs(request: Request, limit: int = 60):
    user = _require_user(request)
    return {"ok": True, "items": db.list_history_docs(user["id"], limit)}


@app.get("/api/history/doc/{rid}")
async def history_doc(rid: str, request: Request):
    user = _require_user(request)
    r = db.get_history_doc(user["id"], rid)
    if not r:
        raise HTTPException(status_code=404, detail="记录不存在或已删除")
    try:
        r["questions"] = json.loads(r.get("qa") or "[]")
    except Exception:
        r["questions"] = []
    r.pop("qa", None)
    return {"ok": True, "item": r}


@app.delete("/api/history/doc/{rid}")
async def history_doc_del(rid: str, request: Request):
    user = _require_user(request)
    return {"ok": db.delete_history_doc(user["id"], rid)}


@app.post("/api/history/session")
async def history_add_session(req: SessionReq, request: Request):
    """保存一次做题总结。finished=False 表示中途结束。"""
    user = _require_user(request)
    sid = db.add_session(user["id"], {
        "doc_label": req.doc_label,
        "total": req.total, "answered": req.answered, "ok": req.ok, "bad": req.bad,
        "choice_ok": req.choice_ok, "choice_bad": req.choice_bad,
        "score": req.score, "max_score": req.max_score,
        "finished": req.finished,
        "aspects": json.dumps(req.aspects or [], ensure_ascii=False),
        "detail": json.dumps(req.detail or [], ensure_ascii=False),
    })
    return {"ok": True, "id": sid}


@app.get("/api/history/sessions")
async def history_sessions(request: Request, limit: int = 60):
    user = _require_user(request)
    return {"ok": True, "items": db.list_sessions(user["id"], limit)}


@app.get("/api/history/session/{sid}")
async def history_session(sid: str, request: Request):
    user = _require_user(request)
    r = db.get_session(user["id"], sid)
    if not r:
        raise HTTPException(status_code=404, detail="记录不存在或已删除")
    for k in ("aspects", "detail"):
        try:
            r[k] = json.loads(r.get(k) or "[]")
        except Exception:
            r[k] = []
    return {"ok": True, "item": r}


@app.delete("/api/history/session/{sid}")
async def history_session_del(sid: str, request: Request):
    user = _require_user(request)
    return {"ok": db.delete_session(user["id"], sid)}


@app.delete("/api/history")
async def history_clear(request: Request):
    """清空当前用户的全部历史。"""
    user = _require_user(request)
    return {"ok": True, "removed": db.clear_history(user["id"])}


# ---------------- 前端静态托管 ----------------
# B1（安全）：只暴露真正的前端资源。项目根下还有 backend/（含 data.db 用户库、
# 全部源码）、tests/、tools/、docs/、data/（分片缓存）等目录，旧版整目录托管
# 会把源码和数据库一起挂到公网（实测 /backend/data.db、/backend/main.py 均可
# 直接下载）。以下白名单外路径一律返回 404，不泄露存在性。
_STATIC_DENY_PREFIXES = ("backend", "data", "tests", "tools", "docs", ".git", "__pycache__", "_venv")
_STATIC_DENY_SUFFIXES = (".py", ".pyc", ".db", ".db-wal", ".db-shm", ".db-journal",
                         ".md", ".zip", ".log", ".bat", ".ps1", ".sh")
_STATIC_DENY_NAMES = ("requirements.txt",)


class SafeStaticFiles(StaticFiles):
    """带黑名单的静态托管：命中敏感目录/后缀直接 404，其余走原逻辑。"""

    def lookup_path(self, path: str):
        p = (path or "").strip("/").replace("\\", "/").lower()
        first = p.split("/", 1)[0] if p else ""
        if (first in _STATIC_DENY_PREFIXES or p in _STATIC_DENY_NAMES
                or p.endswith(_STATIC_DENY_SUFFIXES)):
            return None, ""
        return super().lookup_path(path)


@app.get("/")
async def root():
    return RedirectResponse(url="/login.html")


app.mount("/", SafeStaticFiles(directory=str(FRONT_DIR), html=True), name="static")


# 便于直接 `python main.py` 调试（生产用 uvicorn 启动）
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
