"""厂商主体调研出处（一手源溯源沉淀）：增删 + provenance 分级 + 类型校验。

conftest 每个 test 重载 app.*、noop seed_publishers（表起步为空）。
"""
import pytest


async def _new_entity(client, name="测试主体") -> int:
    return (await client.post("/api/publishers/", json={"name": name})).json()["id"]


@pytest.mark.asyncio
async def test_no_sources_tier_none(client):
    eid = await _new_entity(client)
    e = (await client.get(f"/api/publishers/{eid}")).json()
    assert e["sources"] == []
    assert e["provenance_tier"] == "none"


@pytest.mark.asyncio
async def test_primary_source_sets_tier_primary(client):
    eid = await _new_entity(client)
    r = await client.post(f"/api/publishers/{eid}/sources", json={
        "url": "https://www.qcc.com/firm/xxx", "title": "企查查工商登记",
        "source_type": "registry", "confidence": "high", "as_of": "2026-06-09",
    })
    assert r.status_code == 201
    s = r.json()
    assert s["is_primary"] is True
    e = (await client.get(f"/api/publishers/{eid}")).json()
    assert e["provenance_tier"] == "primary"
    assert len(e["sources"]) == 1


@pytest.mark.asyncio
async def test_secondary_only_tier_secondary(client):
    eid = await _new_entity(client)
    r = await client.post(f"/api/publishers/{eid}/sources", json={
        "url": "https://example.com/news", "title": "行业媒体报道", "source_type": "media",
    })
    assert r.json()["is_primary"] is False
    e = (await client.get(f"/api/publishers/{eid}")).json()
    assert e["provenance_tier"] == "secondary"


@pytest.mark.asyncio
async def test_mixed_sources_tier_primary(client):
    eid = await _new_entity(client)
    await client.post(f"/api/publishers/{eid}/sources", json={"url": "https://m", "source_type": "media"})
    await client.post(f"/api/publishers/{eid}/sources", json={"url": "https://r", "source_type": "official_domain"})
    e = (await client.get(f"/api/publishers/{eid}")).json()
    assert e["provenance_tier"] == "primary"  # 有一手就是 primary


@pytest.mark.asyncio
async def test_invalid_source_type_422(client):
    eid = await _new_entity(client)
    r = await client.post(f"/api/publishers/{eid}/sources", json={
        "url": "https://x", "source_type": "rumor",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_source_recomputes_tier(client):
    eid = await _new_entity(client)
    prim = (await client.post(f"/api/publishers/{eid}/sources", json={"url": "https://r", "source_type": "registry"})).json()
    await client.post(f"/api/publishers/{eid}/sources", json={"url": "https://m", "source_type": "media"})
    assert (await client.get(f"/api/publishers/{eid}")).json()["provenance_tier"] == "primary"

    # 删掉唯一一手源 → 降级为 secondary（仅剩媒体）
    await client.delete(f"/api/publishers/{eid}/sources/{prim['id']}")
    e = (await client.get(f"/api/publishers/{eid}")).json()
    assert e["provenance_tier"] == "secondary"
    assert len(e["sources"]) == 1


@pytest.mark.asyncio
async def test_sources_and_tier_in_list(client):
    eid = await _new_entity(client, "列表溯源")
    await client.post(f"/api/publishers/{eid}/sources", json={
        "url": "https://sec.gov/x", "source_type": "official_filing",
    })
    lst = (await client.get("/api/publishers/")).json()
    e = next(x for x in lst if x["name"] == "列表溯源")
    assert e["provenance_tier"] == "primary"
    assert e["sources"][0]["source_type"] == "official_filing"


@pytest.mark.asyncio
async def test_add_source_to_missing_entity_404(client):
    r = await client.post("/api/publishers/99999/sources", json={"url": "https://x", "source_type": "media"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cjk_source_fields(client):
    eid = await _new_entity(client)
    await client.post(f"/api/publishers/{eid}/sources", json={
        "url": "https://aiqicha.baidu.com/company", "title": "爱企查·股权穿透",
        "source_type": "registry", "note": "母公司持股 99.97%，法人代表某某",
    })
    e = (await client.get(f"/api/publishers/{eid}")).json()
    s = e["sources"][0]
    assert s["title"] == "爱企查·股权穿透"
    assert "母公司持股" in s["note"]


@pytest.mark.asyncio
async def test_delete_entity_removes_sources(client):
    eid = await _new_entity(client, "待删带源")
    await client.post(f"/api/publishers/{eid}/sources", json={"url": "https://r", "source_type": "registry"})
    assert (await client.delete(f"/api/publishers/{eid}")).status_code == 200
    # 主体没了；往已删主体加源应 404（级联已清子行，不会留孤儿）
    assert (await client.post(f"/api/publishers/{eid}/sources", json={"url": "https://x", "source_type": "media"})).status_code == 404
