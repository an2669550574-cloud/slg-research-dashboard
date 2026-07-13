"""每日 digest 整卡 golden 快照（维护者卡 + 领导卡）。

digest 是本项目的产品本体（推送 > 看板），build_daily_digest 段落拼装强耦合（release_alerts.py
2000+ 行）——现有 test_dingtalk_alerts.py 是**段级**断言，改一处段序 / emoji / TL;DR 计数可能
全绿却让整卡排版静默错位。这里锁**整卡文本**：喂固定 per_combo（异动四类 + 新品 is_slg 真/假各一
+ 厂商新品 + 空 combo），断言 maintainer / leader 两卡渲染逐字符 == golden 文件。

覆盖的结构不变量：段序（TL;DR → 市场标题 → 榜单异动 → 新品上架 → 厂商新品 → 页脚图例）、
维护者 vs 领导差异（领导剥 is_slg=false 新厂线索 + 无「建议建档」+ 无页脚 + TL;DR 新品计数 2→1）。

**改动 digest 排版后**（有意的）重新生成 golden：
    GOLDEN_UPDATE=1 pytest tests/test_digest_golden.py
生成后务必人眼核对 golden 文件 diff 再提交。
"""
import os
import pathlib

import pytest

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden"


def _sample_per_combo():
    """代表性 per_combo：US/iOS 异动四类 + 新品(is_slg 真/假各一) + 厂商新品；JP 空 combo 不出段。"""
    movement = {
        "new_entrants": [{"app_id": "123", "name": "寒霜启示录", "prev_rank": None, "cur_rank": 3}],
        "surges": [{"app_id": "456", "name": "Last War", "prev_rank": 18, "cur_rank": 3}],
        "drops": [{"app_id": "789", "name": "旧王朝", "prev_rank": 5, "cur_rank": None}],
        "revenue_spikes": [{"app_id": "123", "name": "寒霜启示录", "cur_rank": 3,
                            "prev_revenue": 10000, "cur_revenue": 14500, "pct": 45.0}],
        "climbs": [],
    }
    market = {"newcomers": [
        {"app_id": "999", "rank": 12, "name": "神秘新游", "publisher": "Mystery Studio",
         "revenue": 123000, "downloads": 5200, "is_slg": False},
        {"app_id": "com.slg.real", "rank": 8, "name": "钢铁黎明", "publisher": "江娱互动",
         "revenue": 220000, "downloads": 9100, "is_slg": True},
    ]}
    publisher = {"newcomers": [{"entity_name": "江娱互动", "name": "Top Heroes 顶级英雄", "rank": 77}]}
    return [
        {"country": "US", "platform": "ios", "movement": movement,
         "market": market, "publisher": publisher},
        {"country": "JP", "platform": "ios", "movement": None, "market": None, "publisher": None},
    ]


def _build(audience: str) -> str:
    from app.services.release_alerts import build_daily_digest
    res = build_daily_digest(_sample_per_combo(), "2026-06-14", audience=audience)
    assert res is not None, "固定 fixture 不该渲染成空卡"
    return res[1]  # markdown 正文


@pytest.mark.parametrize("audience", ["maintainer", "leader"])
def test_digest_card_golden(audience, monkeypatch):
    from app.config import settings
    # 固定影响排版的配置，避免 settings 默认变动让 golden flake。
    monkeypatch.setattr(settings, "DASHBOARD_BASE_URL", "https://board.example.com")
    monkeypatch.setattr(settings, "DIGEST_MAX_ITEMS", 50)
    monkeypatch.setattr(settings, "DIGEST_MOVEMENT_TOPN", 20)

    text = _build(audience)
    golden = GOLDEN_DIR / f"digest_{audience}.md"

    if os.environ.get("GOLDEN_UPDATE"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden.write_text(text, encoding="utf-8")
        pytest.skip(f"regenerated {golden.name}")

    assert golden.exists(), (
        f"缺 golden 文件 {golden}。首次生成：GOLDEN_UPDATE=1 pytest tests/test_digest_golden.py")
    expected = golden.read_text(encoding="utf-8")
    assert text == expected, (
        f"digest {audience} 卡整卡文本变了。若为有意改动，重新生成并人眼核对 diff：\n"
        f"  GOLDEN_UPDATE=1 pytest tests/test_digest_golden.py")
