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

def test_daily_digest_empty_returns_none():
    from app.services.release_alerts import build_daily_digest
    per_combo = [{"country": "US", "platform": "ios",
                  "movement": None, "market": None, "publisher": None}]
    assert build_daily_digest(per_combo, "2026-06-14") is None


def test_daily_digest_human_readable_no_machine_codes():
    """人话化：不出现 [NEW]/[UP] 机器码；关键数字加粗；多 combo 分段。"""
    from app.services.release_alerts import build_daily_digest
    movement = {
        "new_entrants": [{"app_id": "123", "name": "寒霜启示录", "prev_rank": None, "cur_rank": 3}],
        "surges": [{"app_id": "456", "name": "Last War", "prev_rank": 18, "cur_rank": 3}],
        "drops": [{"app_id": "789", "name": "旧王朝", "prev_rank": 5, "cur_rank": None}],
        "revenue_spikes": [{"app_id": "123", "name": "寒霜启示录", "cur_rank": 3,
                            "prev_revenue": 10000, "cur_revenue": 14500, "pct": 45.0}],
    }
    market = {"newcomers": [{"app_id": "999", "rank": 12, "name": "神秘新游", "publisher": "Mystery Studio",
                             "revenue": 123000, "downloads": 5200, "is_slg": False}]}
    publisher = {"newcomers": [{"entity_name": "江娱互动", "name": "Top Heroes 顶级英雄", "rank": 77}]}
    per_combo = [
        {"country": "US", "platform": "ios", "movement": movement,
         "market": market, "publisher": publisher},
        {"country": "JP", "platform": "ios", "movement": None, "market": None, "publisher": None},
    ]
    title, text, btns = build_daily_digest(per_combo, "2026-06-14")
    assert title == "每日情报 2026-06-14"
    assert "[NEW]" not in text and "[UP]" not in text and "[DOWN]" not in text
    assert "🆕 **寒霜启示录** 空降 **#3**" in text
    assert "📈 **Last War** #18 → **#3**（↑15）" in text
    assert "📉 **旧王朝** 跌出 Top 榜（#5 → 榜外）" in text
    assert "💰 **寒霜启示录** 现 #3 · 收入 **+45%**" in text  # 收入异动带当前名次参照
    assert "✨ **神秘新游** 空降 **#12**" in text and "新厂商待识别" in text
    # A4：新厂商线索（is_slg=false）文案带行动指引 + 行内商店页直达（不挤底部按钮名额）
    assert "新厂商待识别 · 建议建档" in text
    assert "🔗 [查看商店页](https://apps.apple.com/us/app/id999)" in text
    # 富化子行（引用块）：日收入压缩 K/M、下载量、厂商归属（未匹配主体退回发行商名）
    assert "> 日收入 $123K · 下载 5K · 厂商 Mystery Studio" in text
    assert "🏢 **江娱互动** 新品 **Top Heroes 顶级英雄** #77" in text
    assert "🇺🇸 美国 · iOS 畅销榜" in text and "JP" not in text  # 中文市场标签；空 combo 不出段
    # 分组小标题：异动与新品分区
    assert "【榜单异动】" in text and "【新品上架】" in text
    # iOS 数字 app_id → 商店页按钮
    assert ("寒霜启示录 →", "https://apps.apple.com/us/app/id123") in btns


def test_daily_digest_filters_reentries_from_newcomer_lines():
    """build_newcomer_lines 跳过 is_reentry=True 的项——治 weekly combo 老 SLG 产品
    跌出 baseline 又回来被误报"新品"刷屏 digest 的真噪声（实测 JP/android 单 combo
    23 项里 22 项是回归）。先过滤再 [:10] 截断，避免回归占满名额把真首发挤掉。"""
    from app.services.release_alerts import build_newcomer_lines

    market = {"newcomers": [
        {"app_id": "true_new", "rank": 5, "name": "真首发", "publisher": "X", "is_slg": True, "is_reentry": False},
        {"app_id": "back_x",   "rank": 6, "name": "回归老游 1", "publisher": "X", "is_slg": True, "is_reentry": True},
        {"app_id": "back_y",   "rank": 7, "name": "回归老游 2", "publisher": "X", "is_slg": True, "is_reentry": True},
    ]}
    publisher = {"newcomers": [
        {"app_id": "pub_back", "entity_name": "江娱", "name": "Top War 回归", "rank": 80, "is_reentry": True},
        {"app_id": "pub_new", "entity_name": "江娱", "name": "Top Heroes 真首发", "rank": 90, "is_reentry": False},
    ]}
    lines = build_newcomer_lines(market, publisher)
    text = "\n".join(lines)
    # 真首发都在
    assert "真首发" in text and "Top Heroes 真首发" in text
    # 回归全部被砍
    assert "回归老游" not in text and "Top War 回归" not in text


def test_newcomer_lines_lead_cta_and_store_link():
    """A4：is_slg=false 线索行升级——文案带「建议建档」+ 行内商店页直达；
    is_slg=true 不加 CTA；拼不出商店页（缺 country/platform）时只升级文案、不报错。"""
    from app.services.release_alerts import build_newcomer_lines

    market = {"newcomers": [
        {"app_id": "999", "rank": 12, "name": "陌生新游", "publisher": "无名工作室", "is_slg": False},
        {"app_id": "888", "rank": 8, "name": "已知 SLG", "publisher": "大厂", "is_slg": True},
    ]}
    lines = build_newcomer_lines(market, {"newcomers": []}, country="US", platform="ios")
    text = "\n".join(lines)
    # 线索：文案 CTA + iOS 数字 id 拼 App Store 链接
    assert "陌生新游" in text and "新厂商待识别 · 建议建档" in text
    assert "🔗 [查看商店页](https://apps.apple.com/us/app/id999)" in text
    # 已识别 SLG：不打 CTA、不加链接
    assert "已知 SLG" in text
    known_line = next(l for l in lines if "已知 SLG" in l)
    assert "新厂商待识别" not in known_line and "查看商店页" not in known_line

    # 缺 country/platform（老调用/单测）：文案照常升级，链接优雅省略，不抛
    lines2 = build_newcomer_lines(market, {"newcomers": []})
    text2 = "\n".join(lines2)
    assert "新厂商待识别 · 建议建档" in text2 and "查看商店页" not in text2


def test_daily_digest_treats_missing_is_reentry_as_true_first():
    """no_baseline combo 的 newcomer 行不带 is_reentry 字段——按 .get() 拿 None
    = falsy = 真首发处理（兼容性保底，避免冷库 combo 数据被全过滤）。"""
    from app.services.release_alerts import build_newcomer_lines

    market = {"newcomers": [
        {"app_id": "x", "rank": 1, "name": "冷库新品", "publisher": "P", "is_slg": True},
    ]}
    lines = build_newcomer_lines(market, {"newcomers": []})
    assert any("冷库新品" in l for l in lines)


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
    title, text, btns = build_appstore_digest([(App(), "壳木游戏 Camel Games", "Camel HK")])
    assert "🆕 **测试新游：远古纪元** — 壳木游戏 Camel Games（App Store）" in text
    assert "Strategy" in text and "上架 2026-06-12" in text
    assert "仅 PH/CA 可见（疑似软启动）" in text
    assert ("测试新游：远古纪元 →", "https://apps.apple.com/us/app/id123") in btns

    # us 在列 → 普通可见区措辞；GP 行 → Google Play 标且无可见区话术
    class GlobalApp(App):
        storefronts = "us,ph,ca"
    _, text2, _ = build_appstore_digest([(GlobalApp(), "壳木游戏 Camel Games", "Camel HK")])
    assert "可见区 US/PH/CA" in text2 and "软启动" not in text2

    class GpApp(App):
        storefronts = "gp"
        track_view_url = "https://play.google.com/store/apps/details?id=com.test.x"
    _, text3, _ = build_appstore_digest([(GpApp(), "GAME SPARK", None)])
    assert "（Google Play）" in text3 and "可见区" not in text3 and "软启动" not in text3


def test_appstore_digest_expanded_section():
    """扩区上线（软启动 → 新增区域）单独成段，可与新上架并存或单独触发。"""
    from app.services.release_alerts import build_appstore_digest

    class App:
        name = "寒霜远征"
        release_date = "2026-05-01"
        track_view_url = None
        genre = None
        storefronts = "us,ph,ca"
    title, text, btns = build_appstore_digest([], [(App(), "点点互动测试", ["us"])])
    assert title == "商店雷达上新"
    assert "扩区上线" in text
    assert "🌍 **寒霜远征** — 点点互动测试 新增 **US**（现 US/PH/CA）" in text
    assert btns == []


# ── 挂钩链路 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_daily_digest_end_to_end(client, monkeypatch):
    """造当日榜单数据 + 已建档主体 → 日报含异动与两层新品，且只发一条。"""
    import importlib
    from datetime import timedelta
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking

    today = utcnow_naive().strftime("%Y-%m-%d")
    prev = (utcnow_naive() - timedelta(days=1)).strftime("%Y-%m-%d")

    r = await client.post("/api/publishers/", json={
        "name": "江娱互动测试", "aliases": [{"keyword": "river game"}]})
    assert r.status_code == 201

    async with AsyncSessionLocal() as db:
        rows = [
            ("veteran", prev, 1, "Century Games Pte. Ltd."),
            ("veteran", today, 1, "Century Games Pte. Ltd."),
            ("rookie", today, 4, "Mystery Studio"),                    # 全市场新面孔
            ("topheroes", today, 88, "River Game HK Limited"),         # 厂商新品(88名)
        ]
        for app_id, date, rank, pub in rows:
            db.add(GameRanking(app_id=app_id, date=date, rank=rank, downloads=None,
                               revenue=None, country="US", platform="ios",
                               name=app_id, publisher=pub, icon_url=None))
        await db.commit()

    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    sent = []
    async def fake_card(title, text, btns=None):
        sent.append((title, text, btns))
        return True
    monkeypatch.setattr(dt, "send_action_card", fake_card)

    assert await ra.send_daily_digest() is True
    assert len(sent) == 1
    _, text, _ = sent[0]
    assert "rookie" in text
    assert "🏢 **江娱互动测试** 新品 **topheroes** #88" in text


@pytest.mark.asyncio
async def test_alerts_test_endpoint_disabled(client, monkeypatch):
    # 强制清掉 webhook 配置，隔离本地 backend/.env 里配了真实 webhook 的情况
    monkeypatch.setattr("app.config.settings.DINGTALK_WEBHOOK_URL", "", raising=False)
    r = await client.post("/api/alerts/dingtalk/test")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "sent": False}


def test_store_url_gp_and_ios():
    """_store_url：iOS 数字 id → App Store；安卓包名(含 .) → Google Play；其余 None。"""
    from app.services.release_alerts import _store_url
    assert _store_url("123", "us", "ios") == "https://apps.apple.com/us/app/id123"
    assert _store_url("com.dequ.m3dw2", "jp", "android") == \
        "https://play.google.com/store/apps/details?id=com.dequ.m3dw2"
    assert _store_url("not numeric", "us", "ios") is None      # iOS 非数字拼不出
    assert _store_url("nopackage", "us", "android") is None    # 安卓无 . 不是包名


def test_daily_digest_newcomer_and_android_buttons():
    """纯新品日（无 movement）也产出商店按钮；安卓包名拼 GP 链接。"""
    from app.services.release_alerts import build_daily_digest
    market = {"newcomers": [{"app_id": "com.x.y", "rank": 4, "name": "安卓新游",
                             "publisher": "P", "is_slg": False, "is_reentry": False}]}
    per_combo = [{"country": "JP", "platform": "android", "movement": None,
                  "market": market, "publisher": None}]
    _, _, btns = build_daily_digest(per_combo, "2026-06-14")
    assert ("安卓新游 →", "https://play.google.com/store/apps/details?id=com.x.y") in btns


def test_daily_digest_movement_cap(monkeypatch):
    """单 combo 的 movement 行被 DIGEST_MOVEMENT_TOPN 封顶，超额进折叠行。"""
    from app.services import release_alerts as ra
    monkeypatch.setattr("app.config.settings.DIGEST_MOVEMENT_TOPN", 2, raising=False)
    movement = {
        "new_entrants": [{"app_id": "1", "name": "AA", "prev_rank": None, "cur_rank": 1},
                         {"app_id": "2", "name": "BB", "prev_rank": None, "cur_rank": 2}],
        "surges": [{"app_id": "3", "name": "CC", "prev_rank": 10, "cur_rank": 3}],  # 第3条超 cap
        "drops": [], "revenue_spikes": [],
    }
    per_combo = [{"country": "US", "platform": "ios", "movement": movement,
                  "market": None, "publisher": None}]
    _, text, _ = ra.build_daily_digest(per_combo, "2026-06-14")
    assert "**AA**" in text and "**BB**" in text
    assert "**CC**" not in text                      # movement 第3条被 cap=2 砍
    assert "另有 **1** 项未在此展示" in text
    assert "（3 项）" in text                          # 标题 total 仍是真实总数


def test_daily_digest_global_cap_overflow(monkeypatch):
    """全局 DIGEST_MAX_ITEMS 封顶：超出的整 combo 折叠成「另有 N 项」，标题 total 不变。"""
    from app.services import release_alerts as ra
    monkeypatch.setattr("app.config.settings.DIGEST_MAX_ITEMS", 1, raising=False)
    def mk(n):
        return {"newcomers": [{"app_id": str(n), "rank": n, "name": f"游{n}",
                               "publisher": "P", "is_slg": True, "is_reentry": False}]}
    per_combo = [
        {"country": "US", "platform": "ios", "movement": None, "market": mk(1), "publisher": None},
        {"country": "JP", "platform": "ios", "movement": None, "market": mk(2), "publisher": None},
    ]
    _, text, _ = ra.build_daily_digest(per_combo, "2026-06-14")
    assert "游1" in text and "游2" not in text        # 第二 combo 被全局封顶
    assert "另有 **1** 项未在此展示" in text
    assert "（2 项）" in text                          # total 仍计全部


def test_dashboard_focus_link(monkeypatch):
    """A4：配了 DASHBOARD_BASE_URL → 新品行带「看板定位」深链（?focus=<app_id>&view=），
    市场行 view=market、厂商行 view=publisher；overflow 行做成看板深链。"""
    from app.services import release_alerts as ra
    monkeypatch.setattr("app.config.settings.DASHBOARD_BASE_URL",
                        "https://board.example.com/", raising=False)  # 末尾斜杠应被裁掉
    market = {"newcomers": [
        {"app_id": "123", "rank": 5, "name": "市场新游", "publisher": "P", "is_slg": True, "is_reentry": False},
    ]}
    publisher = {"newcomers": [
        {"app_id": "com.x.y", "entity_name": "江娱", "name": "厂商新游", "rank": 9, "is_reentry": False},
    ]}
    lines = ra.build_newcomer_lines(market, publisher, country="US", platform="ios")
    text = "\n".join(lines)
    assert "🎯 [看板定位](https://board.example.com/newcomers?focus=123&view=market)" in text
    assert "🎯 [看板定位](https://board.example.com/newcomers?focus=com.x.y&view=publisher)" in text
    # overflow 折叠行做成深链
    monkeypatch.setattr("app.config.settings.DIGEST_MAX_ITEMS", 1, raising=False)
    per_combo = [
        {"country": "US", "platform": "ios", "movement": None, "market": market, "publisher": None},
        {"country": "JP", "platform": "ios", "movement": None,
         "market": {"newcomers": [{"app_id": "999", "rank": 2, "name": "游2", "publisher": "P", "is_slg": True, "is_reentry": False}]},
         "publisher": None},
    ]
    _, dtext, _ = ra.build_daily_digest(per_combo, "2026-06-14")
    assert "[看板查看全部](https://board.example.com/newcomers)" in dtext


def test_dashboard_focus_link_omitted_when_unset(monkeypatch):
    """未配 DASHBOARD_BASE_URL（默认空）→ 不拼任何看板深链，digest 向后兼容。"""
    from app.services import release_alerts as ra
    monkeypatch.setattr("app.config.settings.DASHBOARD_BASE_URL", "", raising=False)
    market = {"newcomers": [
        {"app_id": "123", "rank": 5, "name": "市场新游", "publisher": "P", "is_slg": True, "is_reentry": False},
    ]}
    lines = ra.build_newcomer_lines(market, {"newcomers": []}, country="US", platform="ios")
    assert "看板定位" not in "\n".join(lines)
