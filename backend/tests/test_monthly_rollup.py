"""SLG 市场月报 build_monthly_market_rollup（月度复盘 rollup，读时算 game_rankings，零 ST）。

覆盖：段①名次净变动（上升/下降 + 跨度门控 + is_slg 过滤）+ 段②新品存活小结 + 两段空 → None
+ 新品不重复进段①（段①段②去重）。段①靠 **live is_slg**（只读 game_rankings、无存档 is_slg
列可依赖）→ 用 monkeypatch 控制判定，确定性、不依赖 seed 索引。

注意：所有 app.* 必须在函数内 import —— conftest 的 app 夹具会先清空 sys.modules 再用临时 DB
重新装载 engine，模块顶层 import 会绑到旧（默认）DB 上，导致写入落到本地 dev 库、读取落到
临时库（症状：build 读到空 → 返回 None）。
"""
import pytest
from datetime import timedelta


async def _add_ranking(app_id, rank, days_ago, name=None, publisher="Acme",
                       country="US", platform="ios", chart_type=None):
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking, CHART_GROSSING
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(
            app_id=app_id,
            date=(utcnow_naive() - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            rank=rank, country=country, platform=platform,
            chart_type=chart_type or CHART_GROSSING,
            name=name or app_id, publisher=publisher,
        ))
        await db.commit()


async def _add_newcomer(app_id, name, rank, days_ago, is_slg=True, publisher="Acme",
                        subgenre_cn=None):
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.newcomer import MarketNewcomerLog
    from app.models.game import CHART_GROSSING
    async with AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(
            app_id=app_id, name=name, country="US", platform="ios",
            chart_type=CHART_GROSSING, rank=rank, is_slg=is_slg, publisher=publisher,
            subgenre_cn=subgenre_cn,
            first_detected_at=utcnow_naive() - timedelta(days=days_ago),
            as_of=(utcnow_naive() - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
        ))
        await db.commit()


def _slg_prefix_only(monkeypatch):
    """让 live is_slg 只认 com.slg.* 前缀（段①判定 target；确定性、不依赖 seed 索引）。"""
    monkeypatch.setattr("app.services.slg_publishers.is_slg",
                        lambda app_id, publisher=None: str(app_id).startswith("com.slg."))


@pytest.mark.asyncio
async def test_monthly_rank_movers_up_and_down(client, monkeypatch):
    """SLG 竞品窗口首末名次对比：上升进 ↑ 段、下降进 ↓ 段。"""
    from app.services import release_alerts as ra
    _slg_prefix_only(monkeypatch)
    # 上升：#40（28天前）→ #12（1天前），跨度 27 天 >= max(7, 30//3)=10
    await _add_ranking("com.slg.up", rank=40, days_ago=28, name="Riser")
    await _add_ranking("com.slg.up", rank=12, days_ago=1, name="Riser")
    # 下降：#8 → #25
    await _add_ranking("com.slg.down", rank=8, days_ago=28, name="Faller")
    await _add_ranking("com.slg.down", rank=25, days_ago=1, name="Faller")
    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is not None
    title, body = card
    assert "市场月报" in title
    assert "Riser" in body and "#40" in body and "#12" in body and "↑28" in body
    assert "Faller" in body and "↓17" in body


@pytest.mark.asyncio
async def test_monthly_rank_movers_span_gate(client, monkeypatch):
    """窗口内点跨度不足（相邻两天）→ 不算月度变动（噪声过滤）。"""
    from app.services import release_alerts as ra
    _slg_prefix_only(monkeypatch)
    await _add_ranking("com.slg.blip", rank=40, days_ago=2, name="Blip")
    await _add_ranking("com.slg.blip", rank=10, days_ago=1, name="Blip")  # 跨度 1 天 < 10
    # 段①空（跨度不足）+ 段②空（无新品）→ None
    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is None


@pytest.mark.asyncio
async def test_monthly_rank_movers_excludes_non_slg(client, monkeypatch):
    """非 SLG 竞品名次变动不进段①。"""
    from app.services import release_alerts as ra
    _slg_prefix_only(monkeypatch)  # com.other.* 判 False
    await _add_ranking("com.other.big", rank=40, days_ago=28, name="NonSLG")
    await _add_ranking("com.other.big", rank=5, days_ago=1, name="NonSLG")
    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is None


@pytest.mark.asyncio
async def test_monthly_newcomer_survival_section(client, monkeypatch):
    """段②：近窗口 SLG 新品存活分层（起飞明细 + 计数）。"""
    from app.services import release_alerts as ra
    _slg_prefix_only(monkeypatch)
    # 起飞新品：检出 #50（10天前）→ 现 #20（1天前）；grossing 跨度 9 天 < 10 → 不进段①，只进段②
    await _add_newcomer("com.slg.newclimb", "NewClimber", rank=50, days_ago=10)
    await _add_ranking("com.slg.newclimb", rank=50, days_ago=10, name="NewClimber")
    await _add_ranking("com.slg.newclimb", rank=20, days_ago=1, name="NewClimber")
    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is not None
    _, body = card
    assert "新品存活" in body and "NewClimber" in body
    assert "🚀 起飞" in body


@pytest.mark.asyncio
async def test_monthly_rollup_none_when_empty(client, monkeypatch):
    """两段都无内容 → None（不发空卡）。"""
    from app.services import release_alerts as ra
    _slg_prefix_only(monkeypatch)
    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is None


@pytest.mark.asyncio
async def test_monthly_newcomer_excluded_from_movers(client, monkeypatch):
    """近窗口新品不重复进段①名次净变动（归段②新品存活）——回归防线。"""
    from app.services import release_alerts as ra
    _slg_prefix_only(monkeypatch)
    # 新品：检出 #50（28天前）→ #10（1天前），grossing 跨度 27 天 >= 10，本会进段①；
    # 但它是新品（有 MarketNewcomerLog）→ 应只在段②起飞明细出现，不重复进段①。
    await _add_newcomer("com.slg.dup", "DupGame", rank=50, days_ago=28)
    await _add_ranking("com.slg.dup", rank=50, days_ago=28, name="DupGame")
    await _add_ranking("com.slg.dup", rank=10, days_ago=1, name="DupGame")
    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is not None
    _, body = card
    assert "新品存活" in body and "DupGame" in body
    assert body.count("DupGame") == 1, "新品应只在段②出现一次，不重复进段①"
    seg1 = body.split("🌱")[0]  # 段②以 🌱 起头；🌱 之前 = 段①/表头
    assert "DupGame" not in seg1, "段①名次净变动不应含新品"


@pytest.mark.asyncio
async def test_monthly_subgenre_pulse_section(client, monkeypatch):
    """段③赛道升降温：近窗口新品按子品类分布 + 环比（升温 ↑ / 降温 ↓）。

    用 is_slg=False 的新品隔离——段①（无 rankings）、段②（非 is_slg 不入 reps）都空，
    只剩段③；验证 pulse 计算（compute_subgenre_pulse 共用逻辑）在月度卡里正确渲染。
    """
    from app.services import release_alerts as ra
    # 当前窗口（≤30 天）：数字门SLG ×2、基地建设SLG ×1
    await _add_newcomer("com.x.a1", "游戏1", rank=50, days_ago=5, is_slg=False, subgenre_cn="数字门SLG")
    await _add_newcomer("com.x.a2", "游戏2", rank=60, days_ago=8, is_slg=False, subgenre_cn="数字门SLG")
    await _add_newcomer("com.x.a3", "游戏3", rank=70, days_ago=10, is_slg=False, subgenre_cn="基地建设SLG")
    # 上一窗口（30~60 天）：数字门SLG ×1（→ 数字门环比 +1）+ 国战SLG ×1（本窗口无 → 降温 -1）
    await _add_newcomer("com.x.a4", "游戏4", rank=80, days_ago=40, is_slg=False, subgenre_cn="数字门SLG")
    await _add_newcomer("com.x.a5", "游戏5", rank=90, days_ago=45, is_slg=False, subgenre_cn="国战SLG")
    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is not None
    _, body = card
    assert "赛道升降温" in body
    assert "数字门SLG" in body and "2 款新品" in body and "↑1" in body   # 升温
    assert "基地建设SLG" in body and "1 款新品" in body
    assert "国战SLG" in body and "↓1" in body                          # 降温（本窗口 0 款）


@pytest.mark.asyncio
async def test_monthly_group_activity_section(client, monkeypatch):
    """段④资本集团动态：近窗口 SLG 新品按资本系聚合。

    建「元趣系」母子两主体（affiliate 并组、根打 group_label）+ 各挂 alias 让新品归属命中，
    同集团两款新品应聚合成一行、显示组名与新品样例。独立主体的新品不进本段。
    """
    from app.services import release_alerts as ra
    from app.services.slg_publishers import load_index_from_db
    # 母 = 元趣娱乐（打组名）；子 = 江娱互动（affiliate）；各配 alias 命中 publisher 串
    parent = (await client.post("/api/publishers/", json={
        "name": "元趣娱乐", "is_slg": True, "aliases": [{"keyword": "yuanqu"}]})).json()
    child = (await client.post("/api/publishers/", json={
        "name": "江娱互动", "is_slg": True, "aliases": [{"keyword": "rivergame"}]})).json()
    await client.post(f"/api/publishers/{parent['id']}/relations", json={
        "counterpart_id": child["id"], "counterpart_role": "child", "relation_type": "affiliate"})
    await client.put(f"/api/publishers/{parent['id']}", json={"group_label": "元趣系"})
    solo = (await client.post("/api/publishers/", json={
        "name": "独立小厂", "is_slg": True, "aliases": [{"keyword": "soloslg"}]})).json()
    await load_index_from_db()

    # 归属命中靠 publisher 串（含 alias keyword）；用非上榜口径隔离段①（无 rankings）
    await _add_newcomer("com.a.one", "元趣新品甲", rank=40, days_ago=5, publisher="Yuanqu Pte")
    await _add_newcomer("com.a.two", "江娱新品乙", rank=45, days_ago=7, publisher="RiverGame HK")
    await _add_newcomer("com.a.solo", "独立新品", rank=50, days_ago=9, publisher="SoloSLG Ltd")

    card = await ra.build_monthly_market_rollup(days=30, cap=5)
    assert card is not None
    _, body = card
    assert "资本集团动态" in body
    # 元趣系聚合两款、显示组名
    assert "元趣系" in body and "2 款新品" in body
    assert "元趣新品甲" in body and "江娱新品乙" in body
    # 独立主体不进集团段（它归属了主体但主体无集团）
    assert "独立新品" not in body.split("资本集团动态")[1]
