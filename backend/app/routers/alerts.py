"""/api/alerts/ —— 告警通道自检。

钉钉 webhook 配置在 backend/.env（DINGTALK_WEBHOOK_URL / DINGTALK_SECRET），
不进 git；这里只提供"配了没有 + 能不能发出去"的自检端点，不回显任何配置值。
"""
from fastapi import APIRouter

from app.services import dingtalk

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("/dingtalk/test")
async def test_dingtalk():
    """发一条测试消息到钉钉群。未配置 webhook → enabled=False 直接返回。"""
    if not dingtalk.is_enabled():
        return {"enabled": False, "sent": False}
    sent = await dingtalk.send_markdown(
        "告警通道测试",
        "### ✅ SLG 看板 · 钉钉告警通道连通\n\n"
        "之后这三类消息会推到本群：\n\n"
        "- 🆕 新品监测（全市场空降 / 厂商新品进榜）——随各市场榜单同步触达\n\n"
        "- 📱 App Store 新上架（开发者清单 diff）——每周一同步后触达\n\n"
        "- 📊 竞品异动（SLG 白名单进退榜 / 收入异动）——随榜单同步触达",
    )
    return {"enabled": True, "sent": sent}
