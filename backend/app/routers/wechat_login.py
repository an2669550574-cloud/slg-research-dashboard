"""/api/wechat-login/* —— 透明反代 wechat-api 的扫码登录流，让维护者在看板里
（钉钉过期提醒按钮直达）扫码续期微信公众号登录态，**免 ssh 隧道**。

链路（与 wechat-api 自带 login.html 同一套，逐个透传）：
  POST /session/{sid}  → wechat-api POST /api/login/session/{sid}（初始化会话、种 cookie）
  GET  /getqrcode      → 二维码图（PNG/JPEG；前端 fetch 成 blob 显示，<img src> 带不了鉴权头）
  GET  /scan           → 轮询扫码状态（status：1=成功 / 4,6=已扫待确认 / 2=过期 / 3=失败）
  POST /bizlogin       → 扫码确认后完成登录（wechat-api 落 session）
  GET  /status         → 当前登录态（前端进页先看是否真过期）

会话 cookie 由**浏览器同源承载**：本代理把 wechat-api 的 Set-Cookie 原样回传浏览器
（看板域名下存），后续请求浏览器自动带回、代理再转发给 wechat-api——无需服务端会话状态。
全部端点挂 _protected（看板 API_KEY），不新增对 wechat-api 的裸公网暴露。零 ST 配额。
"""
import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import settings

router = APIRouter(prefix="/api/wechat-login", tags=["wechat-login"])

# 只转发这些请求头给 wechat-api（cookie 承载会话；content-type 让 body 正确解析）。
# 不转发 host/content-length 等逐跳头，避免 httpx 与上游打架。
_FWD_REQ_HEADERS = {"cookie", "content-type"}


async def _proxy(request: Request, method: str, upstream_path: str) -> Response:
    """把当前请求透传到 wechat-api 的 upstream_path，双向转发 cookie。"""
    if not settings.WECHAT_ENABLED:
        raise HTTPException(status_code=409, detail="微信功能未启用（WECHAT_ENABLED=false）")
    url = f"{settings.WECHAT_API_BASE.rstrip('/')}{upstream_path}"
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() in _FWD_REQ_HEADERS}
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            up = await client.request(method, url, params=dict(request.query_params),
                                      content=body or None, headers=fwd_headers)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"wechat-api 不可达：{e}")

    resp = Response(content=up.content, status_code=up.status_code,
                    media_type=up.headers.get("content-type"))
    # 透传上游全部 Set-Cookie（可能多条）——让浏览器在看板域名下存住微信会话 cookie。
    for key, val in up.headers.raw:
        if key.lower() == b"set-cookie":
            resp.headers.append("set-cookie", val.decode("latin-1"))
    return resp


@router.post("/session/{sid}")
async def wechat_login_session(sid: str, request: Request):
    return await _proxy(request, "POST", f"/api/login/session/{sid}")


@router.get("/getqrcode")
async def wechat_login_qrcode(request: Request):
    return await _proxy(request, "GET", "/api/login/getqrcode")


@router.get("/scan")
async def wechat_login_scan(request: Request):
    return await _proxy(request, "GET", "/api/login/scan")


@router.post("/bizlogin")
async def wechat_login_bizlogin(request: Request):
    return await _proxy(request, "POST", "/api/login/bizlogin")


@router.get("/status")
async def wechat_login_status(request: Request):
    return await _proxy(request, "GET", "/api/admin/status")
