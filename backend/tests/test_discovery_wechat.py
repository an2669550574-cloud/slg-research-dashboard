"""发现层·公众号抽取扫描（期5a）测试。全 mock，零外网/零 LLM/零 ST。"""


def test_name_match():
    from app.services.discovery_triage import _name_match
    assert _name_match("Last Duo: Survival", "Last Duo: Survival") is True
    assert _name_match("Desire City", "Desire City") is True
    assert _name_match("Foo", "Foo Bar Baz") is True
    assert _name_match("Last Duo", "Totally Unrelated Game") is False
    assert _name_match("", "x") is False


def test_no_sensor_tower_import():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    for rel in ("services/discovery_wechat.py", "services/discovery_triage.py"):
        src = (root / rel).read_text(encoding="utf-8")
        imports = "\n".join(l for l in src.splitlines() if re.match(r"\s*(from|import)\b", l))
        assert "sensor_tower" not in imports.lower(), f"{rel} 违反零 ST 铁律"


async def test_scan_session_dead_gate(app, monkeypatch):
    from app.services import discovery_wechat as dw

    async def _dead():
        return False
    monkeypatch.setattr(dw, "probe_articles_alive", _dead)
    res = await dw.scan()
    assert res["alive"] is False and res["candidates"] == []
    assert "扫码" in res["reason"] or "session" in res["reason"].lower()


async def test_scan_extracts_filters_resolves_covers(app, monkeypatch):
    from app.services import discovery_wechat as dw

    async def _alive():
        return True

    async def _list(fakeid, count):
        return [{"title": "新游《Foo SLG》", "digest": "厂商推出", "link": "http://x",
                 "create_time": 9999999999}]

    async def _extract(title, digest):
        return [
            {"name": "Foo SLG", "publisher": "Foo Studio", "platform": "android",
             "genre": "基建SLG", "slg_relevant": True},
            {"name": "Bar Casual", "publisher": "B", "platform": "ios",
             "genre": "消除", "slg_relevant": False},   # 非 SLG，应被滤
        ]

    async def _resolve(name, platform_hint=None):
        return {"app_id": "com.foo.slg", "platform": "android", "store_name": "Foo SLG",
                "store_url": "http://gp", "match": True}

    async def _cov(app_id):
        return "unknown"

    monkeypatch.setattr(dw, "probe_articles_alive", _alive)
    monkeypatch.setattr(dw, "_list_recent", _list)
    monkeypatch.setattr(dw, "extract_products", _extract)
    monkeypatch.setattr(dw, "resolve_name_to_store", _resolve)
    monkeypatch.setattr(dw, "_coverage", _cov)

    res = await dw.scan(days=7, per_account=3)
    assert res["alive"] is True
    # 4 源 × 1 文 × 2 抽取（1 SLG）→ extracted=8, slg=4
    assert res["stats"]["extracted"] == 8
    assert res["stats"]["slg"] == 4
    assert res["stats"]["unknown_slg"] == 4
    cands = res["candidates"]
    assert len(cands) == 4
    assert all(c["name"] == "Foo SLG" for c in cands)         # 非 SLG 已滤
    assert cands[0]["coverage"] == "unknown"
    assert cands[0]["resolved"]["app_id"] == "com.foo.slg"
