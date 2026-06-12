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
        "之后这两类消息会推到本群：\n\n"
        "- 📡 **每日情报汇总**（竞品异动 + 新品监测，全市场合并一条）——每天北京时间 11:00 左右\n\n"
        "- 🛒 **商店雷达上新**（重点厂商 App Store / Google Play 清单 diff）——每 6 小时检出即推",
    )
    return {"enabled": True, "sent": sent}
