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
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL_LEADER", "", raising=False)
    called = []
    monkeypatch.setattr(dt, "_post_payload", lambda payload, **kw: called.append(payload))
    assert await dt.send_markdown("标题", "内容") is False
    assert called == []


@pytest.mark.asyncio
async def test_send_adds_keyword_prefix_and_swallows_errors(monkeypatch):
    import importlib
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")

    sent = []
    async def ok(payload, **kw):
        sent.append(payload)
        return True
    monkeypatch.setattr(dt, "_post_payload", ok)
    assert await dt.send_markdown("竞品异动 US/ios", "text") is True
    assert sent[0]["markdown"]["title"] == "SLG · 竞品异动 US/ios"  # 关键词前缀

    async def boom(payload, **kw):
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
    assert "💻 [商店页](https://apps.apple.com/us/app/id999)" in text
    # 富化子行（引用块）：日收入压缩 K/M、下载量、厂商归属（未匹配主体退回发行商名）
    assert "> 日收入 $123K · 下载 5K · 厂商 Mystery Studio" in text
    assert "🏢 **江娱互动** 新品 **Top Heroes 顶级英雄** #77" in text
    assert "🇺🇸 美国 · iOS" in text and "JP" not in text  # 中文市场标签；空 combo 不出段
    # 分组小标题：异动与新品分区
    assert "【榜单异动】" in text and "【新品上架】" in text
    # 按钮改看板深链（需 DASHBOARD_BASE_URL）；本测试未配 → 无按钮，ActionCard 降级 markdown
    assert btns == []


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
    assert "💻 [商店页](https://apps.apple.com/us/app/id999)" in text
    # 已识别 SLG：不打 CTA、不加链接
    assert "已知 SLG" in text
    known_line = next(l for l in lines if "已知 SLG" in l)
    assert "新厂商待识别" not in known_line and "商店页" not in known_line

    # 缺 country/platform（老调用/单测）：文案照常升级，链接优雅省略，不抛
    lines2 = build_newcomer_lines(market, {"newcomers": []})
    text2 = "\n".join(lines2)
    assert "新厂商待识别 · 建议建档" in text2 and "商店页" not in text2


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
    assert "🆕 **测试新游：远古纪元** — 壳木游戏 Camel Games（🍎 App Store）" in text
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
    assert "（🤖 Google Play · 美区视角）" in text3 and "软启动" not in text3


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
    async def fake_card(title, text, btns=None, **kw):
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


def test_daily_digest_newcomer_buttons_use_dashboard(monkeypatch):
    """按钮取头条新品 → 看板深链（两端可达、手机也能点），商店直链改走行内带 💻。
    movement 不进按钮（异动老游戏看板新品页定位不到）。"""
    from app.services.release_alerts import build_daily_digest
    monkeypatch.setattr("app.config.settings.DASHBOARD_BASE_URL",
                        "https://board.example.com", raising=False)
    market = {"newcomers": [{"app_id": "com.x.y", "rank": 4, "name": "安卓新游",
                             "publisher": "P", "is_slg": False, "is_reentry": False}]}
    per_combo = [{"country": "JP", "platform": "android", "movement": None,
                  "market": market, "publisher": None}]
    _, _, btns = build_daily_digest(per_combo, "2026-06-14")
    assert ("安卓新游 →", "https://board.example.com/newcomers?focus=com.x.y&view=market") in btns


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
    assert "📊 异动 3" in text                          # TL;DR 总览计真实总数


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
    assert "✨ 新品 2" in text                          # TL;DR 总览计全部新品（去重）


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
    assert "🎯 [看板](https://board.example.com/newcomers?focus=123&view=market)" in text
    assert "🎯 [看板](https://board.example.com/newcomers?focus=com.x.y&view=publisher)" in text
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
    assert "🎯 [看板]" not in "\n".join(lines)


# ── 方案①：下载榜 is_slg=false 真新厂「待建档线索」段 ─────────────────────────

def test_lead_newcomer_lines_render_and_dedup(monkeypatch):
    """待建档线索行：含名次/中文genre/发行商/看板核查链接 + 中文摘要(#147)，按 app_id 去重；
    市场标签用下载榜语境（不带「畅销榜」后缀，与 _combo_label 区分）。"""
    monkeypatch.setattr("app.config.settings.DASHBOARD_BASE_URL",
                        "https://board.example.com", raising=False)
    from app.services.release_alerts import build_lead_newcomer_lines
    items = [
        {"app_id": "com.x.warz", "name": "Last Shelter: War Z",
         "publisher": "LAST ORIGIN STUDIO LIMITED", "rank": 12,
         "country": "US", "platform": "android", "genre": "Strategy",
         "summary_cn": "末日丧尸生存 SLG，主打基地建造"},
        {"app_id": "com.x.warz", "name": "dup", "publisher": "p", "rank": 99,
         "country": "US", "platform": "android", "genre": "Strategy"},  # 同 app_id → 去重
    ]
    lines = build_lead_newcomer_lines(items)
    assert len(lines) == 1
    assert "Last Shelter: War Z" in lines[0]
    assert "LAST ORIGIN STUDIO LIMITED" in lines[0]
    assert "策略" in lines[0] and "Strategy" not in lines[0]   # genre 中文化（_genre_cn）
    assert "📝 末日丧尸生存 SLG，主打基地建造" in lines[0]      # 中文摘要接入（A / #147）
    assert "看板核查" in lines[0]
    assert "#12" in lines[0]
    assert "畅销榜" not in lines[0]          # 下载榜段不能误用收入榜标签
    assert "下载榜" in lines[0]


def test_lead_newcomer_lines_degrade_without_summary():
    """译文未就位（summary_cn 缺省）→ 优雅降级，不显 📝，其余照常。"""
    from app.services.release_alerts import build_lead_newcomer_lines
    lines = build_lead_newcomer_lines([
        {"app_id": "com.y.z", "name": "Untranslated", "publisher": "p", "rank": 5,
         "country": "JP", "platform": "ios", "genre": "Strategy"},  # 无 summary_cn
    ])
    assert len(lines) == 1 and "📝" not in lines[0] and "策略" in lines[0]


def test_daily_digest_lead_section_alone_still_sends():
    """只有待建档线索、无其它情报 → 仍发卡（不漏）且出现该段；不传 lead_items 则 None。"""
    from app.services.release_alerts import build_daily_digest
    per_combo = [{"country": "US", "platform": "ios", "movement": None,
                  "market": None, "publisher": None}]
    lead = [{"app_id": "com.a.b", "name": "疑似新厂SLG", "publisher": "无名工作室",
             "rank": 7, "country": "US", "platform": "ios", "genre": "Strategy"}]
    msg = build_daily_digest(per_combo, "2026-06-27", lead_items=lead)
    assert msg is not None
    _, text, _ = msg
    assert "待建档新厂线索" in text
    assert "疑似新厂SLG" in text
    # 同样 per_combo 但不传 lead_items + 无其它情报 → 不发卡（向后兼容）
    assert build_daily_digest(per_combo, "2026-06-27") is None


@pytest.mark.asyncio
async def test_send_daily_digest_lead_section_filters_by_genre(client, monkeypatch):
    """方案①闭环：下载榜 is_slg=false 但 genre=Strategy 的新品进「待建档线索」段（补救
    白名单滞后漏推，LAST ORIGIN STUDIO 症状）；genre=Puzzle 休闲噪声被 genre 初筛压掉。"""
    import importlib
    from datetime import timedelta
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking, CHART_FREE
    from app.models.newcomer import MarketNewcomerLog

    today = utcnow_naive().strftime("%Y-%m-%d")
    prev = (utcnow_naive() - timedelta(days=1)).strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as db:
        free_rows = [
            ("free_vet", prev, 1, "Some Pub"),
            ("free_vet", today, 1, "Some Pub"),
            ("warz_new", today, 5, "Unknown SLG Studio"),   # is_slg=false + Strategy → 进 lead
            ("puzzle_new", today, 6, "Casual Maker"),        # is_slg=false + Puzzle → 滤掉
        ]
        for aid, date, rank, pub in free_rows:
            db.add(GameRanking(app_id=aid, date=date, rank=rank, country="US",
                               platform="ios", name=aid, publisher=pub,
                               chart_type=CHART_FREE))
        db.add(MarketNewcomerLog(country="US", platform="ios", app_id="warz_new",
                                 chart_type="free", as_of=today, name="warz_new",
                                 publisher="Unknown SLG Studio", genre="Strategy", is_slg=False))
        db.add(MarketNewcomerLog(country="US", platform="ios", app_id="puzzle_new",
                                 chart_type="free", as_of=today, name="puzzle_new",
                                 publisher="Casual Maker", genre="Puzzle", is_slg=False))
        await db.commit()

    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    monkeypatch.setattr(settings, "FREE_CHART_COMBOS", "US:ios")
    sent = []
    async def fake_card(title, text, btns=None, **kw):
        sent.append((title, text, btns))
        return True
    monkeypatch.setattr(dt, "send_action_card", fake_card)

    assert await ra.send_daily_digest() is True
    assert len(sent) == 1
    _, text, _ = sent[0]
    assert "待建档新厂线索" in text
    # 名字带 `_` 会被 _md_name 转义(warz\_new)，故断言走稳定的发行商串识别 warz 行
    assert "Unknown SLG Studio" in text   # is_slg=false + Strategy → 浮现给维护者
    assert "Casual Maker" not in text     # genre 初筛压掉休闲噪声(puzzle_new 整行被滤)


# ── 重要度排序（统一打分喂 排序 / 全局封顶 / movement TopN / 按钮 / 今日要闻）──────

def test_digest_importance_event_score_ordering():
    """打分相对序拍定：高名次收入异动 > 头部空降/市场新品 > 大幅窜升 > 榜尾长尾空降/跌出。"""
    from app.services.release_alerts import _event_score
    big_rev = _event_score("revenue_spike", {"cur_rank": 2, "pct": 200})
    top_new = _event_score("new_entrant", {"cur_rank": 1, "prev_rank": None})
    big_surge = _event_score("surge", {"prev_rank": 40, "cur_rank": 5})
    tail_new = _event_score("new_entrant", {"cur_rank": 49, "prev_rank": None})
    tail_drop = _event_score("drop", {"prev_rank": 48, "cur_rank": None})
    assert big_rev > top_new > big_surge > tail_new > tail_drop


def test_digest_importance_market_weight_is_gentle_tilt():
    """市场权重只做轻微倾斜：同事件 US > KR，但**不能**把事件强度整个吃掉——KR 的 #1
    空降必须仍压过 US 的 #45 长尾空降（否则今日要闻会被核心市场榜尾占满）。"""
    from app.services.release_alerts import _event_score, _market_weight
    us_tail = _event_score("new_entrant", {"cur_rank": 45, "prev_rank": None}) * _market_weight("US", "ios")
    kr_top = _event_score("new_entrant", {"cur_rank": 1, "prev_rank": None}) * _market_weight("KR", "ios")
    us_top = _event_score("new_entrant", {"cur_rank": 1, "prev_rank": None}) * _market_weight("US", "ios")
    assert kr_top > us_tail          # 次市场大事件压过核心市场长尾
    assert us_top > kr_top           # 同强度事件 US 仍微微领先


def test_digest_movement_cap_keeps_high_importance(monkeypatch):
    """movement TopN 砍尾按重要度而非类序：末类的大额收入异动不被前类的榜尾长尾挤掉。"""
    from app.services.release_alerts import build_movement_lines
    monkeypatch.setattr("app.config.settings.DIGEST_MOVEMENT_TOPN", 2, raising=False)
    movement = {
        "new_entrants": [{"app_id": str(i), "name": f"长尾{i}", "prev_rank": None, "cur_rank": 45 + i}
                         for i in range(3)],
        "surges": [], "drops": [],
        "revenue_spikes": [{"app_id": "big", "name": "头部巨鳄", "cur_rank": 2,
                            "prev_revenue": 1_000_000, "cur_revenue": 3_000_000, "pct": 200.0}],
    }
    lines = build_movement_lines(movement, cap=2)
    text = "\n".join(lines)
    assert "头部巨鳄" in text                      # 末类大额收入异动保住
    assert lines[0].startswith("💰")               # 且排第一（重要度降序）
    assert "长尾2" not in text                      # 最弱长尾被砍


def test_digest_global_cap_never_drops_core_market(monkeypatch):
    """全局封顶按 combo 重要度（市场权重为主）砍：核心 US/iOS 永远保留，被折叠的是次市场。
    即便次市场 combo 在入参里排在 US 前面（地理顺序乱序），也不影响。"""
    from app.services import release_alerts as ra
    monkeypatch.setattr("app.config.settings.DIGEST_MAX_ITEMS", 1, raising=False)
    def mk(aid, name, rank):
        return {"newcomers": [{"app_id": aid, "rank": rank, "name": name,
                               "publisher": "P", "is_slg": True, "is_reentry": False}]}
    per_combo = [
        {"country": "KR", "platform": "ios", "movement": None, "market": mk("k", "韩新品", 3), "publisher": None},
        {"country": "US", "platform": "ios", "movement": None, "market": mk("u", "美新品", 3), "publisher": None},
    ]
    _, text, _ = ra.build_daily_digest(per_combo, "2026-06-28")
    assert "美新品" in text                          # 核心市场保留
    assert "韩新品" not in text                       # 次市场被全局封顶折叠
    assert "另有 **1** 项未在此展示" in text


def test_digest_highlights_section_pins_top_events_cross_combo():
    """今日要闻：跨 combo 抽最高重要度事件置顶，事件数 > TOPN 时才渲染。"""
    from app.services import release_alerts as ra
    us_mv = {
        "new_entrants": [{"app_id": str(i), "name": f"小新{i}", "prev_rank": None, "cur_rank": 45 + i}
                         for i in range(5)],
        "surges": [], "drops": [],
        "revenue_spikes": [{"app_id": "big", "name": "头部巨鳄", "cur_rank": 2,
                            "prev_revenue": 1_000_000, "cur_revenue": 3_000_000, "pct": 200.0}],
    }
    kr_mv = {"new_entrants": [{"app_id": "kr1", "name": "韩区爆款", "prev_rank": None, "cur_rank": 1}],
             "surges": [], "drops": [], "revenue_spikes": []}
    per_combo = [
        {"country": "KR", "platform": "ios", "movement": kr_mv, "market": None, "publisher": None},
        {"country": "US", "platform": "ios", "movement": us_mv, "market": None, "publisher": None},
    ]
    _, text, _ = ra.build_daily_digest(per_combo, "2026-06-28")
    assert "【📌 今日要闻】" in text
    hi = text.split("【📌 今日要闻】")[1].split("---")[0]
    # 头部收入异动第一、韩区 #1 爆款第二（跨 combo），均压过 US 榜尾长尾
    assert hi.index("头部巨鳄") < hi.index("韩区爆款") < hi.index("小新0")
    assert "🇰🇷 韩国 · iOS 🆕 **韩区爆款**" in hi   # 要闻行内联市场标签


def test_digest_own_match_boosts_highlight_ranking():
    """B：命中「对标我方」的竞品在今日要闻里上浮——榜尾低强度也排到非对标头部强度之上。"""
    from app.services import release_alerts as ra
    mv = {
        "new_entrants": [
            {"app_id": "rival", "name": "丧尸末日战", "prev_rank": None, "cur_rank": 48},  # 低强度
            {"app_id": "plain", "name": "三国新作", "prev_rank": None, "cur_rank": 3},     # 高强度
        ],
        "surges": [], "drops": [], "revenue_spikes": [],
    }
    per_combo = [{"country": "US", "platform": "ios", "movement": mv,
                  "market": None, "publisher": None}]
    # 不加权：头部 #3 排在榜尾 #48 之前
    base = [it["e"]["app_id"] for _, it in ra._collect_scored_items(per_combo)]
    assert base.index("plain") < base.index("rival")
    # 加权：rival 命中对标 → 上浮到 plain 之前（×_OWN_MATCH_BOOST）
    boosted = [it["e"]["app_id"] for _, it in
               ra._collect_scored_items(per_combo, {"rival": "无尽火线"})]
    assert boosted.index("rival") < boosted.index("plain")


def test_digest_highlights_skipped_when_few_items():
    """事件数 ≤ TOPN（小卡）→ 不渲染今日要闻，避免与正文重复。"""
    from app.services import release_alerts as ra
    movement = {"new_entrants": [{"app_id": "1", "name": "唯一新进", "prev_rank": None, "cur_rank": 3}],
                "surges": [], "drops": [], "revenue_spikes": []}
    per_combo = [{"country": "US", "platform": "ios", "movement": movement,
                  "market": None, "publisher": None}]
    _, text, _ = ra.build_daily_digest(per_combo, "2026-06-28")
    assert "今日要闻" not in text
    assert "唯一新进" in text                         # 正文照常


def test_digest_buttons_ranked_by_importance(monkeypatch):
    """按钮全局按重要度取头部新品：次市场高名次新品能挤进 5 名额，不再被地理顺序锁死。
    构造 6 个市场新品（5 个 US 低名次 + 1 个 KR 高名次），KR 高名次必入按钮。"""
    from app.services import release_alerts as ra
    monkeypatch.setattr("app.config.settings.DASHBOARD_BASE_URL",
                        "https://board.example.com", raising=False)
    def us_mk(i):
        return {"newcomers": [{"app_id": f"us{i}", "rank": 40 + i, "name": f"美新{i}",
                               "publisher": "P", "is_slg": True, "is_reentry": False}]}
    kr = {"newcomers": [{"app_id": "kr_top", "rank": 1, "name": "韩区头部新品",
                         "publisher": "P", "is_slg": True, "is_reentry": False}]}
    per_combo = [{"country": "US", "platform": "ios", "movement": None,
                  "market": us_mk(i), "publisher": None} for i in range(5)]
    per_combo.append({"country": "KR", "platform": "ios", "movement": None,
                      "market": kr, "publisher": None})
    _, _, btns = ra.build_daily_digest(per_combo, "2026-06-28")
    assert len(btns) == 5
    labels = [b[0] for b in btns]
    assert "韩区头部新品 →" in labels                # 高名次次市场新品挤进名额
    assert btns[0][0] == "韩区头部新品 →"            # 且排第一（重要度最高）


def test_digest_does_not_mutate_input_order():
    """build_daily_digest 内部排序用副本，不 mutate 入参 per_combo 顺序。"""
    from app.services import release_alerts as ra
    def mk(aid, name):
        return {"newcomers": [{"app_id": aid, "rank": 3, "name": name,
                               "publisher": "P", "is_slg": True, "is_reentry": False}]}
    per_combo = [
        {"country": "KR", "platform": "ios", "movement": None, "market": mk("k", "韩"), "publisher": None},
        {"country": "US", "platform": "ios", "movement": None, "market": mk("u", "美"), "publisher": None},
    ]
    ra.build_daily_digest(per_combo, "2026-06-28")
    assert [c["country"] for c in per_combo] == ["KR", "US"]   # 入参顺序不变


# ── P0-2: 游戏名/厂商名 markdown 转义 + 截断 ──────────────────────────────────

def test_md_name_escapes_and_truncates():
    """ST 原始名里的 markdown 格式字符被转义/替换，长名被截断——防卡片破版。"""
    from app.services.release_alerts import _md_name
    # 方括号 → 圆括号(防误拼链接)，* _ ` ~ \ 转义(防加粗错位/代码块)
    assert _md_name("War [Beta] *X* _v2_") == r"War (Beta) \*X\* \_v2\_"
    assert _md_name("a`b~c\\d") == r"a\`b\~c\\d"
    # 折叠换行/多空白
    assert _md_name("Last   War\nZ") == "Last War Z"
    # 超长截断带省略号；maxlen=None 不截
    assert _md_name("A" * 40).endswith("…") and len(_md_name("A" * 40)) == 32
    assert _md_name("A" * 40, maxlen=None) == "A" * 40
    # 空/None 安全
    assert _md_name(None) == "" and _md_name("") == ""


def test_digest_escapes_game_name_no_broken_markdown():
    """端到端: 含 [Beta]/* 的脏名进 digest 不破版——加粗不错位、方括号不成死链。"""
    from app.services.release_alerts import build_movement_lines
    mv = {"new_entrants": [{"app_id": "1", "name": "Doom [Beta] *Z*",
                            "prev_rank": None, "cur_rank": 3}],
          "surges": [], "drops": [], "revenue_spikes": []}
    line = build_movement_lines(mv)[0]
    assert "**Doom (Beta) \\*Z\\***" in line   # 外层加粗完整、内部 * 已转义
    assert "[Beta]" not in line                  # 方括号已替换，不会误成链接


# ── P0-1/P0-4: 领导卡受众剥离 + 双发路由 + 主卡失败升 Sentry ────────────────────

def test_digest_leader_audience_strips_maintainer_noise():
    """领导卡剥离维护者杂讯（待建档段/建议建档尾标/TLDR 待建档计数），但保留竞品情报。"""
    from app.services.release_alerts import build_daily_digest
    market = {"newcomers": [{"app_id": "999", "rank": 12, "name": "陌生新游",
                             "publisher": "无名工作室", "is_slg": False, "is_reentry": False}]}
    publisher = {"newcomers": [{"app_id": "p1", "entity_name": "莉莉丝", "name": "已识别SLG新品",
                                "rank": 8, "is_reentry": False}]}
    per_combo = [{"country": "US", "platform": "ios", "movement": None,
                  "market": market, "publisher": publisher}]
    lead = [{"app_id": "com.a.b", "name": "疑似新厂SLG", "publisher": "无名工作室",
             "rank": 7, "country": "US", "platform": "ios", "genre": "Strategy"}]
    _, m_text, _ = build_daily_digest(per_combo, "2026-06-28", lead_items=lead, audience="maintainer")
    _, l_text, _ = build_daily_digest(per_combo, "2026-06-28", lead_items=lead, audience="leader")
    # maintainer：维护者杂讯齐全（待建档段 + 待识别 market 新品 + 建议建档尾标）
    assert "待建档新厂线索" in m_text and "建议建档" in m_text and "🔍 待建档" in m_text
    assert "陌生新游" in m_text
    # leader：维护者杂讯全剥离
    assert "待建档新厂线索" not in l_text
    assert "建议建档" not in l_text
    assert "🔍 待建档" not in l_text
    # 竞品情报保留：已识别 SLG 厂 publisher 新品在；但 is_slg=false「待识别新厂」整段剥离
    #（口径「领导只看 SLG 产品」——待识别含足球/塔防/恐怖等非 SLG 噪声，对领导无用）
    assert "已识别SLG新品" in l_text
    assert "陌生新游" not in l_text


@pytest.mark.asyncio
async def test_send_daily_digest_dual_send_routing(client, monkeypatch):
    """配了 leader webhook → 同一检测数据发两张卡: maintainer 版 + leader 版，各走各群，
    均 critical=True。专测路由，build_daily_digest 打桩返回带 audience 标记的文案。"""
    import importlib
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/maintainer")
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL_LEADER", "https://example.com/leader", raising=False)
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    monkeypatch.setattr(ra, "build_daily_digest",
                        lambda *a, audience="maintainer", **k: ("t", f"CARD::{audience}", []))
    sent = []
    async def fake_card(title, text, btns=None, target="maintainer", critical=False):
        sent.append((text, target, critical))
        return True
    monkeypatch.setattr(dt, "send_action_card", fake_card)
    assert await ra.send_daily_digest() is True
    by_target = {t: (text, crit) for text, t, crit in sent}
    assert set(by_target) == {"maintainer", "leader"}
    assert by_target["maintainer"][0] == "CARD::maintainer"
    assert by_target["leader"][0] == "CARD::leader"
    assert by_target["maintainer"][1] is True and by_target["leader"][1] is True  # 主卡 critical


@pytest.mark.asyncio
async def test_send_daily_digest_single_send_without_leader(client, monkeypatch):
    """未独立配 leader webhook → 只发 maintainer 一张（向后兼容），不把领导版重发回维护者群。"""
    import importlib
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/maintainer")
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL_LEADER", "", raising=False)
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    monkeypatch.setattr(ra, "build_daily_digest",
                        lambda *a, audience="maintainer", **k: ("t", f"CARD::{audience}", []))
    sent = []
    async def fake_card(title, text, btns=None, target="maintainer", critical=False):
        sent.append(target)
        return True
    monkeypatch.setattr(dt, "send_action_card", fake_card)
    assert await ra.send_daily_digest() is True
    assert sent == ["maintainer"]


@pytest.mark.asyncio
async def test_critical_send_failure_logs_error(monkeypatch, caplog):
    """critical=True 的发送终态失败打 logger.error（进 Sentry）；critical=False 仍 warning。"""
    import importlib, logging
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    async def boom(payload, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(dt, "_post_payload", boom)
    with caplog.at_level(logging.WARNING):
        await dt.send_action_card("t", "x", [("a", "https://x")], critical=False)
        await dt.send_action_card("t", "x", [("a", "https://x")], critical=True)
    fails = [r.levelno for r in caplog.records if "send failed" in r.getMessage()]
    assert logging.ERROR in fails and logging.WARNING in fails


def test_leader_target_falls_back_but_configured_flag_strict(monkeypatch):
    """_target_fields('leader') 未配时回退 maintainer（任意调用方不报错）；
    但 leader_target_configured() 严格判，未配返回 False（digest 据此不双发）。"""
    import importlib
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://m/hook")
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL_LEADER", "", raising=False)
    assert dt.leader_target_configured() is False
    assert dt._target_fields("leader")[0] == "https://m/hook"   # 回退 maintainer
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL_LEADER", "https://l/hook", raising=False)
    assert dt.leader_target_configured() is True
    assert dt._target_fields("leader")[0] == "https://l/hook"   # 独立群


# ── 同赛道：竞品和我方哪款同赛道（子品类精确 / 关键词回退 → digest 标签）──────────

def test_match_own_product_substring_and_priority():
    """未配子品类的产品走关键词回退：小写子串、命中名字/摘要、第一个产品优先、空安全。"""
    from app.services.release_alerts import _match_own_product
    prods = [("极寒纪元", ["丧尸", "末日", "survival"], set()), ("三国志", ["三国"], set())]
    assert _match_own_product("Last War: Survival", None, prods) == ("极寒纪元", "survival")  # 大小写无关
    assert _match_own_product("一款丧尸末日生存策略", None, prods) == ("极寒纪元", "丧尸")
    assert _match_own_product("三国名将传", None, prods) == ("三国志", "三国")
    assert _match_own_product("Candy Match 3", None, prods) is None
    assert _match_own_product("", None, prods) is None
    assert _match_own_product("survival", None, []) is None


def test_match_own_product_subgenre_authoritative():
    """配了 match_subgenre → 只按子品类相等匹配（忽略关键词，治题材太宽泛）；竞品无/异子品类不命中。"""
    from app.services.release_alerts import _match_own_product
    # 无尽火线只认「数字门SLG」（即便关键词留着也被忽略）
    prods = [("无尽火线", ["丧尸", "末日", "survival"], {"数字门SLG"})]
    # 真数字门竞品（子品类命中）→ 命中，即使文本不含任何题材词
    assert _match_own_product("Some Game", "数字门SLG", prods) == ("无尽火线", "数字门SLG")
    # 基地建设竞品（同末日题材、文本含 survival/末日）→ 子品类不符 → **不命中**（正是要去掉的假阳）
    assert _match_own_product("Last Shelter survival 末日丧尸", "基地建设SLG", prods) is None
    # 竞品未分类（subgenre=None）+ 文本含题材词 → 仍不命中（子品类权威、不回退关键词）
    assert _match_own_product("丧尸末日 survival", None, prods) is None


def test_match_own_product_keyword_fallback_when_no_subgenre():
    """产品未配子品类 → 回退题材关键词（兼容老配置，竞品有无子品类都走关键词）。"""
    from app.services.release_alerts import _match_own_product
    prods = [("三国志", ["三国"], set())]
    assert _match_own_product("三国名将传", None, prods) == ("三国志", "三国")
    assert _match_own_product("三国名将传", "国战SLG", prods) == ("三国志", "三国")


def test_digest_own_product_tag_and_tldr():
    """对标命中 → 竞品行尾「⚔️《X》同赛道」(movement + 新品两处) + TL;DR「⚔️ 同赛道 N」置顶。"""
    from app.services.release_alerts import build_daily_digest
    mv = {"new_entrants": [{"app_id": "1", "name": "Zombie War", "prev_rank": None, "cur_rank": 3}],
          "surges": [], "drops": [], "revenue_spikes": []}
    market = {"newcomers": [{"app_id": "2", "rank": 8, "name": "Apocalypse",
                             "publisher": "P", "is_slg": True, "is_reentry": False}]}
    per = [{"country": "US", "platform": "ios", "movement": mv, "market": market, "publisher": None}]
    om = {"1": "极寒纪元", "2": "极寒纪元"}
    _, text, _ = build_daily_digest(per, "2026-06-28", own_matches=om)
    assert "🆕 **Zombie War** 空降 **#3**（榜外 →） ⚔️《极寒纪元》同赛道" in text   # movement 行
    assert "✨ **Apocalypse** 空降 **#8** ⚔️《极寒纪元》同赛道" in text             # 新品行
    assert "⚔️ 同赛道 2" in text                                                  # TL;DR 计数置顶
    # 不传 own_matches → 完全无对标标签（向后兼容）
    _, text2, _ = build_daily_digest(per, "2026-06-28")
    assert "同赛道" not in text2 and "⚔️" not in text2


def test_digest_own_match_shows_on_both_audiences():
    """对标是纯决策信号，领导卡与维护者卡都显示（不像待建档那样剥离）。"""
    from app.services.release_alerts import build_daily_digest
    market = {"newcomers": [{"app_id": "2", "rank": 8, "name": "X",
                             "publisher": "P", "is_slg": True, "is_reentry": False}]}
    per = [{"country": "US", "platform": "ios", "movement": None, "market": market, "publisher": None}]
    om = {"2": "极寒纪元"}
    for aud in ("maintainer", "leader"):
        _, text, _ = build_daily_digest(per, "2026-06-28", own_matches=om, audience=aud)
        assert "⚔️《极寒纪元》同赛道" in text


@pytest.mark.asyncio
async def test_load_own_products_filters_splits_lowercases(client):
    """_load_own_products: 关键词 trim+小写拆分、子品类拆分(保留原样)；关键词与子品类**都空**才跳过。"""
    from app.services.release_alerts import _load_own_products
    from app.database import AsyncSessionLocal
    from app.models.product import OwnProduct
    async with AsyncSessionLocal() as db:
        db.add(OwnProduct(name="极寒纪元对标测试", brief="b", match_keywords="丧尸, 末日 ,Survival"))
        db.add(OwnProduct(name="数字门产品测试", brief="b", match_subgenre="数字门SLG"))   # 只配子品类
        db.add(OwnProduct(name="无关键词对标测试", brief="b", match_keywords=None))
        db.add(OwnProduct(name="空白词对标测试", brief="b", match_keywords="  ,  "))
        await db.commit()
    prods = await _load_own_products()
    assert ("极寒纪元对标测试", ["丧尸", "末日", "survival"], set()) in prods   # trim + lowercase
    assert any(n == "数字门产品测试" and subs == {"数字门SLG"} for n, _, subs in prods)  # 只配子品类也收
    assert all(kws or subs for _, kws, subs in prods)                  # 关键词/子品类至少一个非空
    names = {n for n, _, _ in prods}
    assert "无关键词对标测试" not in names and "空白词对标测试" not in names


def test_digest_video_lines_caps_and_folds():
    """① 实机视频段：超过 cap 条只详列前 N，其余折叠成一行汇总（不静默丢）。"""
    from app.services.release_alerts import build_video_lines

    items = [{"name": f"新品{i}", "count": 5, "url": f"https://y/{i}"} for i in range(8)]
    lines = build_video_lines(items, 5)
    detailed = [l for l in lines if l.startswith("🎬")]
    assert len(detailed) == 5                       # 只详列前 5
    fold = [l for l in lines if "另有" in l]
    assert len(fold) == 1 and "**3**" in fold[0]    # 8 - 5 = 3 折叠
    # 不超 cap：不折叠、全列
    lines2 = build_video_lines(items[:4], 5)
    assert len(lines2) == 4 and all("另有" not in l for l in lines2)


def test_digest_market_lead_caps_and_folds():
    """② 市场「待识别新厂」(is_slg=false) 超过 DIGEST_MARKET_LEAD_TOPN 个 → 前 N 详列 + 折叠行；
    已识别 SLG（is_slg=true）不受限量（次市场批量同步日防刷屏，建档线索仍可经折叠行追溯）。"""
    from app.services.release_alerts import build_newcomer_lines
    from app.config import settings

    topn = settings.DIGEST_MARKET_LEAD_TOPN
    leads = [{"app_id": str(i), "rank": i, "name": f"待识别{i}", "publisher": f"厂{i}",
              "is_slg": False, "is_reentry": False} for i in range(1, topn + 4)]   # topn+3 个待识别
    known = {"app_id": "k", "rank": 99, "name": "已识别龙头", "publisher": "大厂",
             "is_slg": True, "is_reentry": False}
    market = {"newcomers": leads + [known]}
    lines = build_newcomer_lines(market, {"newcomers": []}, country="US", platform="ios")
    text = "\n".join(lines)
    shown_leads = [l for l in lines if "新厂商待识别" in l]
    assert len(shown_leads) == topn                       # 待识别只详列前 topn
    fold = [l for l in lines if "未识别新面孔" in l]
    assert len(fold) == 1 and "**3**" in fold[0]          # (topn+3) - topn = 3 折叠
    assert "已识别龙头" in text                             # 已识别不受限，照常显示


def test_digest_leader_excludes_market_lead_newcomers():
    """领导卡口径「只看 SLG 产品」：market 层 is_slg=false「待识别新厂」(足球/塔防/恐怖等
    非 SLG + 未识别真新厂)整段剥离——正文 + TL;DR 计数都不含；已识别 SLG 厂的 publisher
    新品 + is_slg=true market 保留。维护者卡不受影响、待识别照常（口径只作用领导卡）。"""
    from app.services.release_alerts import build_daily_digest

    market = {"newcomers": [
        {"app_id": "slg1", "rank": 5, "name": "已识别SLG市场新品", "publisher": "P", "is_slg": True, "is_reentry": False},
        {"app_id": "td1", "rank": 8, "name": "Tower Defense 塔防新品", "publisher": "Q", "is_slg": False, "is_reentry": False},
        {"app_id": "ball", "rank": 9, "name": "足球竞技手游", "publisher": "R", "is_slg": False, "is_reentry": False},
    ]}
    publisher = {"newcomers": [
        {"app_id": "pub1", "entity_name": "莉莉丝", "name": "已识别厂战争新品", "rank": 12, "is_reentry": False},
    ]}
    per_combo = [{"country": "US", "platform": "ios", "movement": None,
                  "market": market, "publisher": publisher}]

    # 领导卡：待识别(is_slg=false)整段剥离；is_slg=true market + publisher 保留
    _, body_l, _ = build_daily_digest(per_combo, "2026-06-28", audience="leader")
    assert "已识别SLG市场新品" in body_l and "已识别厂战争新品" in body_l
    assert "塔防新品" not in body_l and "足球竞技手游" not in body_l
    assert "新厂商待识别" not in body_l          # 领导卡无待识别标记/折叠行
    assert "✨ 新品 2" in body_l                  # TL;DR 计数也只算 SLG（slg1 + pub1）

    # 维护者卡：不过滤，待识别照常（口径只作用领导卡）
    _, body_m, _ = build_daily_digest(per_combo, "2026-06-28", audience="maintainer")
    assert "塔防新品" in body_m and "足球竞技手游" in body_m
    assert "✨ 新品 4" in body_m                  # 全量计数（3 market + 1 publisher）


# ── 回归门控 P1.4：movement 渲染 + 重要度降权 ────────────────────────────────

def test_movement_reentry_renders_distinct_verb():
    """is_reentry=True → 「🔄 重回」；False/缺字段 → 「🆕 空降」（兼容老结构）。"""
    from app.services.release_alerts import build_movement_lines
    s = {"new_entrants": [
            {"app_id": "r", "name": "老兵回归", "prev_rank": None, "cur_rank": 6, "is_reentry": True},
            {"app_id": "n", "name": "真首发", "prev_rank": None, "cur_rank": 7, "is_reentry": False}],
         "surges": [], "drops": [], "revenue_spikes": []}
    text = "\n".join(build_movement_lines(s))
    assert "🔄 **老兵回归** 重回 **#6**" in text
    assert "🆕 **真首发** 空降 **#7**" in text


def test_movement_reentry_highlight_verb():
    """今日要闻一行同样区分重回/空降。"""
    from app.services.release_alerts import _highlight_line
    e = {"app_id": "r", "name": "老兵", "cur_rank": 2, "is_reentry": True}
    line = _highlight_line({"e": e, "country": "US", "platform": "ios", "kind": "new_entrant"})
    assert "🔄 **老兵** 重回 #2" in line and "空降" not in line


def test_movement_reentry_scored_below_true_entrant():
    """回归降权：同名次 is_reentry 强度分 < 真首发；但仍 >0（高名次回归不硬排除）。"""
    from app.services.release_alerts import _event_score
    true_new = _event_score("new_entrant", {"cur_rank": 3})
    reentry = _event_score("new_entrant", {"cur_rank": 3, "is_reentry": True})
    assert 0 < reentry < true_new
    # #1 回归仍应低于真·头部空降，避免占据今日要闻头部
    top_reentry = _event_score("new_entrant", {"cur_rank": 1, "is_reentry": True})
    top_true = _event_score("new_entrant", {"cur_rank": 1})
    assert top_reentry < top_true


# ── 心跳 / 数据未就位卡 P1.1：纯构造函数 ─────────────────────────────────────

def test_heartbeat_and_data_not_ready_cards():
    from app.services.release_alerts import build_heartbeat_card, build_data_not_ready_card
    h_title, h_text = build_heartbeat_card("2026-06-29")
    assert "2026-06-29" in h_title and "平静" in h_text
    d_title, d_text = build_data_not_ready_card("2026-06-29")
    assert "数据未就位" in d_title
    assert "未同步" in d_text and "美国 · iOS" in d_text


# ── 心跳 / 数据未就位 dispatch P1.1：send_daily_digest 三分支 ─────────────────

async def _seed_us_ios(rows):
    """rows: [(app_id, date, rank, publisher)]，US/ios grossing。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for app_id, date, rank, pub in rows:
            db.add(GameRanking(app_id=app_id, date=date, rank=rank, downloads=None,
                               revenue=None, country="US", platform="ios",
                               name=app_id, publisher=pub, icon_url=None))
        await db.commit()


def _quiet_day_rows():
    """同一 app 在最近 5 个连续快照(含今日)同名次 → 无异动、无新品(已进基线) = 平淡日。"""
    from datetime import timedelta
    from app.database import utcnow_naive
    SLG = "Century Games Pte. Ltd."
    days = [(utcnow_naive() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
    return [("steady", d, 1, SLG) for d in days], days[0]


@pytest.mark.asyncio
async def test_send_daily_digest_quiet_day_silent_when_heartbeat_off(client, monkeypatch):
    """真平淡日(核心已同步、无事) + 心跳关 → 静默不发任何卡（保持默认行为）。"""
    import importlib
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    rows, _ = _quiet_day_rows()
    await _seed_us_ios(rows)
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    monkeypatch.setattr(settings, "DIGEST_HEARTBEAT_ENABLED", False)
    sent = []
    async def fake_card(*a, **k): sent.append(("card", a)); return True
    async def fake_md(*a, **k): sent.append(("md", a)); return True
    monkeypatch.setattr(dt, "send_action_card", fake_card)
    monkeypatch.setattr(dt, "send_markdown", fake_md)
    assert await ra.send_daily_digest() is False
    assert sent == []          # 平淡日 + 心跳关 = 完全静默


@pytest.mark.asyncio
async def test_send_daily_digest_heartbeat_when_enabled(client, monkeypatch):
    """平淡日 + DIGEST_HEARTBEAT_ENABLED 开 → 发一张「平静」心跳卡（markdown）。"""
    import importlib
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    rows, _ = _quiet_day_rows()
    await _seed_us_ios(rows)
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    monkeypatch.setattr(settings, "DIGEST_HEARTBEAT_ENABLED", True)
    md_sent = []
    async def fake_md(title, text, *a, **k): md_sent.append((title, text)); return True
    async def fake_card(*a, **k): raise AssertionError("平淡日不应发 ActionCard")
    monkeypatch.setattr(dt, "send_markdown", fake_md)
    monkeypatch.setattr(dt, "send_action_card", fake_card)
    assert await ra.send_daily_digest() is True
    assert any("平静" in text for _, text in md_sent)


@pytest.mark.asyncio
async def test_send_daily_digest_data_not_ready_alarms(client, monkeypatch, caplog):
    """核心 US/iOS 今日无快照(只有历史) → 升 logger.error(Sentry) + 发数据未就位兜底卡。"""
    import importlib, logging
    from datetime import timedelta
    from app.database import utcnow_naive
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    SLG = "Century Games Pte. Ltd."
    # 只种昨天的数据，不种今天 → today_missing，核心未就位
    prev = (utcnow_naive() - timedelta(days=1)).strftime("%Y-%m-%d")
    await _seed_us_ios([("steady", prev, 1, SLG)])
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    md_sent = []
    async def fake_md(title, text, *a, **k): md_sent.append((title, text)); return True
    monkeypatch.setattr(dt, "send_markdown", fake_md)
    with caplog.at_level(logging.ERROR, logger="app.services.release_alerts"):
        assert await ra.send_daily_digest() is True
    assert any("数据未就位" in title for title, _ in md_sent)
    assert any("core US/iOS snapshot missing" in r.getMessage()
               for r in caplog.records if r.levelno == logging.ERROR)
