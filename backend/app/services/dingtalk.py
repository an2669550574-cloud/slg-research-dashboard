"""钉钉自定义群机器人 webhook 客户端。

- 不配 DINGTALK_WEBHOOK_URL = 整体关闭：send_markdown 静默返回 False，调用方零分支。
- DINGTALK_SECRET 配了走「加签」（timestamp + HMAC-SHA256 base64 urlencode）；
  没配则依赖机器人的「自定义关键词」安全设置——所有标题都带 "SLG" 前缀以命中关键词。
- **发送失败绝不抛异常**（告警是旁路，不能拖垮同步任务本身）；失败打 logger.warning
  （不上 ERROR——webhook 配错时每次同步都 error 会把 Sentry 刷成噪声）。
- 不打印 webhook URL / secret（含 token，属敏感配置）。
"""
import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return bool(settings.DINGTALK_WEBHOOK_URL)


def _signed_url(ts_ms: int | None = None) -> str:
    """带加签参数的 webhook URL。secret 未配置则原样返回。ts_ms 可注入便于测试。"""
    url = settings.DINGTALK_WEBHOOK_URL
    secret = settings.DINGTALK_SECRET
    if not secret:
        return url
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={ts}&sign={sign}"


async def _post_payload(payload: dict) -> bool:
    """实际 HTTP 发送。独立出来便于测试 monkeypatch。"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_signed_url(), json=payload)
        resp.raise_for_status()
        body = resp.json()
    if body.get("errcode") != 0:
        logger.warning("DingTalk webhook rejected message: errcode=%s errmsg=%s",
                       body.get("errcode"), body.get("errmsg"))
        return False
    return True


async def send_markdown(title: str, text: str) -> bool:
    """发一条 markdown 消息。未启用 → False；任何失败 → False + warning 日志。

    title 自动加 "SLG" 前缀（兼容「自定义关键词」安全模式）。
    """
    if not is_enabled():
        return False
    if not title.startswith("SLG"):
        title = f"SLG · {title}"
    try:
        return await _post_payload({
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
        })
    except Exception:
        logger.warning("DingTalk webhook send failed (title=%s)", title, exc_info=True)
        return False
