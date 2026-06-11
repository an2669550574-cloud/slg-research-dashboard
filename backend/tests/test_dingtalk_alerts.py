"""钉钉告警：webhook 客户端 + 新品/异动摘要构建 + 挂钩链路。

核心验证：
- 未配 webhook：send 静默 False、alert_* 全程 no-op、自检端点 enabled=False
- 加签 URL：timestamp+sign 参数齐全且确定性可断言
- digest 构建纯函数：空摘要不发；中文厂商/产品名按预期落进 markdown（CJK 纪律）
- 发送失败/异常吞掉不抛（告警旁路不拖垮同步）
"""
import pytest


# ── webhook 客户端 ──────────────────────────────────────────────────────────

def test_signed_url_deterministic(monkeypatch):
    import importlib
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=tok")
    monkeypatch.setattr(settings, "DINGTALK_SECRET", "SECabc")
    url = dt._signed_url(ts_ms=1700000000000)
    assert url.startswith("https://oapi.dingtalk.com/robot/send?access_token=tok&timestamp=1700000000000&sign=")
    # 同参数稳定
    assert url == dt._signed_url(ts_ms=1700000000000)
    # 不配 secret 原样返回
    monkeypatch.setattr(settings, "DINGTALK_SECRET", "")
    assert dt._signed_url() == "https://oapi.dingtalk.com/robot/send?access_token=tok"


@pytest.mark.asyncio
async def test_send_disabled_noop(monkeypatch):
    import importlib
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "")
    called = []
    monkeypatch.setattr(dt, "_post_payload", lambda payload: called.append(payload))
    assert await dt.send_markdown("标题", "内容") is False
    assert called == []


@pytest.mark.asyncio
async def test_send_adds_keyword_prefix_and_swallows_errors(monkeypatch):
    import importlib
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")

    sent = []
    async def ok(payload):
        sent.append(payload)
        return True
    monkeypatch.setattr(dt, "_post_payload", ok)
    assert await dt.send_markdown("竞品异动 US/ios", "text") is True
    assert sent[0]["markdown"]["title"] == "SLG · 竞品异动 US/ios"  # 关键词前缀

    async def boom(payload):
        raise RuntimeError("network down")
    monkeypatch.setattr(dt, "_post_payload", boom)
    assert await dt.send_markdown("x", "y") is False  # 吞掉异常不抛


# ── digest 构建（纯函数）───────────────────────────────────────────────────

def test_chart_digest_empty_returns_none():
    from app.services.release_alerts import build_chart_digest
    base = {"country": "US", "platform": "ios", "as_of": "2026-06-14", "newcomers": []}
    assert build_chart_digest(base, dict(base)) is None


def test_chart_digest_contains_cjk_items():
    from app.services.release_alerts import build_chart_digest
    market = {"country": "US", "platform": "ios", "as_of": "2026-06-14", "newcomers": [
        {"rank": 12, "name": "寒霜纪元", "publisher": "Unknown Studio", "revenue": 123000, "is_slg": False},
    ]}
    publisher = {"country": "US", "platform": "ios", "as_of": "2026-06-14", "newcomers": [
        {"entity_name": "江娱互动", "name": "Top Heroes 顶级英雄", "rank": 77},
    ]}
    title, text = build_chart_digest(market, publisher)
    assert title == "新品监测 US/ios"
    assert "#12 寒霜纪元" in text and "新厂商待识别" in text
    assert "江娱互动：Top Heroes 顶级英雄 #77" in text
    assert "快照 2026-06-14" in text


def test_appstore_digest():
    from app.services.release_alerts import build_appstore_digest
    assert build_appstore_digest([]) is None
    assert build_appstore_digest([], []) is None

    class App:
        name = "测试新游：远古纪元"
        release_date = "2026-06-12"
        track_view_url = "https://apps.apple.com/us/app/id123"
        genre = "Strategy"
        storefronts = "ph,ca"  # 无 us → 软启动措辞
    title, text = build_appstore_digest([(App(), "壳木游戏 Camel Games", "Camel HK")])
    assert "壳木游戏 Camel Games：测试新游：远古纪元（上架 2026-06-12）" in text
    assert "Strategy" in text
    assert "仅 PH/CA 可见（疑似软启动）" in text
    assert "[App Store](https://apps.apple.com/us/app/id123)" in text

    # us 在列 → 普通可见区措辞
    class GlobalApp(App):
        storefronts = "us,ph,ca"
    _, text2 = build_appstore_digest([(GlobalApp(), "壳木游戏 Camel Games", "Camel HK")])
    assert "可见区 US/PH/CA" in text2 and "软启动" not in text2


def test_appstore_digest_expanded_section():
    """扩区上线（软启动 → 新增区域）单独成段，可与新上架并存或单独触发。"""
    from app.services.release_alerts import build_appstore_digest

    class App:
        name = "寒霜远征"
        release_date = "2026-05-01"
        track_view_url = None
        genre = None
        storefronts = "us,ph,ca"
    title, text = build_appstore_digest([], [(App(), "点点互动测试", ["us"])])
    assert title == "App Store 雷达"
    assert "扩区上线" in text
    assert "点点互动测试：寒霜远征 新增 US（现 US/PH/CA）" in text


# ── 挂钩链路 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_chart_newcomers_end_to_end(client, monkeypatch):
    """造榜单数据 + 已建档主体 → alert 推送的 markdown 含两层内容。"""
    import importlib
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking

    r = await client.post("/api/publishers/", json={
        "name": "江娱互动测试", "aliases": [{"keyword": "river game"}]})
    assert r.status_code == 201

    async with AsyncSessionLocal() as db:
        rows = [
            ("veteran", "2026-05-08", 1, "Century Games Pte. Ltd."),
            ("veteran", "2026-05-15", 1, "Century Games Pte. Ltd."),
            ("rookie", "2026-05-15", 4, "Mystery Studio"),                  # 全市场新面孔
            ("topheroes", "2026-05-15", 88, "River Game HK Limited"),       # 厂商新品(88名)
        ]
        for app_id, date, rank, pub in rows:
            db.add(GameRanking(app_id=app_id, date=date, rank=rank, downloads=None,
                               revenue=None, country="US", platform="ios",
                               name=app_id, publisher=pub, icon_url=None))
        await db.commit()

    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    captured = {}
    async def fake_send(title, text):
        captured["title"], captured["text"] = title, text
        return True
    monkeypatch.setattr(dt, "send_markdown", fake_send)

    assert await ra.alert_chart_newcomers("US", "ios") is True
    assert "rookie" in captured["text"]
    assert "江娱互动测试：topheroes #88" in captured["text"]


@pytest.mark.asyncio
async def test_alerts_test_endpoint_disabled(client):
    r = await client.post("/api/alerts/dingtalk/test")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "sent": False}
