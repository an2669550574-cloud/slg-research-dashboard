"""钉钉自定义群机器人 webhook 客户端。

- **两个 target**：`maintainer`（默认，= 测试群/运维群，收全量）与 `leader`（领导群，
  收剥离杂讯的精简 digest）。维护者类提醒（微信重登 / 商店雷达 / 自检）一律 maintainer，
  只有每日 digest 主卡会分别发两个群——见 release_alerts.send_daily_digest。
- 不配 maintainer 的 DINGTALK_WEBHOOK_URL = 整体关闭：send 静默返回 False，调用方零分支。
  leader 未独立配（DINGTALK_WEBHOOK_URL_LEADER 空）时 target='leader' **回退到 maintainer**
  （任意调用方不会因没配领导群而报错）；但 digest 双发用 leader_target_configured() 严格判，
  未配就不发那张领导卡，避免把领导版卡重发进 maintainer 群。
- DINGTALK_SECRET 配了走「加签」（timestamp + HMAC-SHA256 base64 urlencode）；没配则依赖
  机器人「自定义关键词」安全设置——所有标题都带 "SLG" 前缀以命中关键词。
- **发送失败绝不抛异常**（告警是旁路，不能拖垮同步任务本身）。失败默认打 logger.warning
  （webhook 配错时每次同步都 error 会把 Sentry 刷成噪声）；**关键发送**（每日 digest 主卡，
  `critical=True`）失败改打 logger.error → 进 Sentry，让维护者立刻知道"领导该收到的卡丢了"。
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


def _target_fields(target: str = "maintainer") -> tuple[str, str, str]:
    """(url, secret, label) by target。leader 未独立配 webhook 时回退 maintainer 字段。"""
    if target == "leader" and settings.DINGTALK_WEBHOOK_URL_LEADER:
        return (settings.DINGTALK_WEBHOOK_URL_LEADER, settings.DINGTALK_SECRET_LEADER,
                settings.DINGTALK_WEBHOOK_LABEL_LEADER or "领导群")
    return (settings.DINGTALK_WEBHOOK_URL, settings.DINGTALK_SECRET,
            settings.DINGTALK_WEBHOOK_LABEL or "默认")


def is_enabled(target: str = "maintainer") -> bool:
    return bool(_target_fields(target)[0])


def leader_target_configured() -> bool:
    """领导群是否**独立**配了 webhook（区别于回退到 maintainer）。digest 双发据此决定
    是否真往领导群发第二张卡——未配则不发，避免把领导版卡重发进 maintainer 群。"""
    return bool(settings.DINGTALK_WEBHOOK_URL_LEADER)


def _signed_url(url: str | None = None, secret: str | None = None,
                ts_ms: int | None = None) -> str:
    """带加签参数的 webhook URL。url/secret 缺省读 maintainer 配置（保持旧调用兼容）；
    secret 未配置则原样返回。ts_ms 可注入便于测试。"""
    if url is None:
        url = settings.DINGTALK_WEBHOOK_URL
    if secret is None:
        secret = settings.DINGTALK_SECRET
    if not secret:
        return url
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={ts}&sign={sign}"


async def _post_payload(payload: dict, target: str = "maintainer",
                        critical: bool = False) -> bool:
    """实际 HTTP 发送（按 target 选群）。独立出来便于测试 monkeypatch。
    critical=True 时被拒绝打 error（进 Sentry），否则 warning。"""
    url, secret, label = _target_fields(target)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_signed_url(url, secret), json=payload)
        resp.raise_for_status()
        body = resp.json()
    if body.get("errcode") != 0:
        log = logger.error if critical else logger.warning
        log("DingTalk webhook「%s」rejected: errcode=%s errmsg=%s",
            label, body.get("errcode"), body.get("errmsg"))
        return False
    logger.info("DingTalk sent → 「%s」", label)
    return True


async def send_markdown(title: str, text: str, target: str = "maintainer",
                        critical: bool = False) -> bool:
    """发一条 markdown 消息。未启用 → False；任何失败 → False（critical=True 打 error
    进 Sentry，否则 warning）。title 自动加 "SLG" 前缀（兼容「自定义关键词」安全模式）。
    """
    if not is_enabled(target):
        return False
    if not title.startswith("SLG"):
        title = f"SLG · {title}"
    try:
        return await _post_payload({
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
        }, target=target, critical=critical)
    except Exception:
        (logger.error if critical else logger.warning)(
            "DingTalk webhook send failed (title=%s)", title, exc_info=True)
        return False


async def send_action_card(title: str, text: str,
                           btns: list[tuple[str, str]] | None = None,
                           target: str = "maintainer", critical: bool = False) -> bool:
    """发一条 ActionCard（整卡 + 底部按钮区，观感优于裸 markdown）。

    钉钉 actionCard 要求至少一个按钮——没有可跳的链接时自动降级 send_markdown，
    调用方零分支。按钮最多 5 个（超出截断），title 同样加 "SLG" 前缀命中关键词。
    target 选群、critical 控制失败日志级别（透传 send_markdown / _post_payload）。
    """
    if not btns:
        return await send_markdown(title, text, target=target, critical=critical)
    if not is_enabled(target):
        return False
    if not title.startswith("SLG"):
        title = f"SLG · {title}"
    payload = {
        "msgtype": "actionCard",
        "actionCard": {
            "title": title,
            "text": text,
            # 1-2 个按钮横排更紧凑；3 个以上竖排避免截断。
            "btnOrientation": "1" if len(btns) <= 2 else "0",
            "btns": [{"title": t, "actionURL": u} for t, u in btns[:5]],
        },
    }
    try:
        return await _post_payload(payload, target=target, critical=critical)
    except Exception:
        (logger.error if critical else logger.warning)(
            "DingTalk actionCard send failed (title=%s)", title, exc_info=True)
        return False
