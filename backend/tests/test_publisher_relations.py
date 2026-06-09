"""主体间股权/母子关系：增删 + 双向视图 + 自环/重复/校验。

conftest 每个 test 重载 app.*、noop seed_publishers（表起步为空）。
"""
import pytest


async def _new_entity(client, name) -> int:
    return (await client.post("/api/publishers/", json={"name": name})).json()["id"]


@pytest.mark.asyncio
async def test_add_parent_relation_both_sides(client):
    a = await _new_entity(client, "子公司A")
    b = await _new_entity(client, "母公司B")
    # A 视角：对方 B 是我的母公司
    r = await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "parent",
        "relation_type": "controlling", "stake_pct": 99.97,
    })
    assert r.status_code == 201
    link = r.json()
    assert link["entity_id"] == b and link["relation_type"] == "controlling"
    assert link["stake_pct"] == pytest.approx(99.97)

    ea = (await client.get(f"/api/publishers/{a}")).json()
    assert [p["entity_id"] for p in ea["parents"]] == [b]
    assert ea["children"] == []
    # 对侧 B 自动看到 A 是子公司
    eb = (await client.get(f"/api/publishers/{b}")).json()
    assert [c["entity_id"] for c in eb["children"]] == [a]
    assert eb["parents"] == []


@pytest.mark.asyncio
async def test_add_child_relation(client):
    a = await _new_entity(client, "母公司A")
    c = await _new_entity(client, "子公司C")
    r = await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": c, "counterpart_role": "child", "relation_type": "wholly_owned",
    })
    assert r.status_code == 201
    ea = (await client.get(f"/api/publishers/{a}")).json()
    assert [x["entity_id"] for x in ea["children"]] == [c]
    cc = (await client.get(f"/api/publishers/{c}")).json()
    assert [x["entity_id"] for x in cc["parents"]] == [a]


@pytest.mark.asyncio
async def test_relation_link_carries_name(client):
    a = await _new_entity(client, "甲")
    b = await _new_entity(client, "乙集团")
    await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "parent", "relation_type": "minority", "stake_pct": 20,
    })
    ea = (await client.get(f"/api/publishers/{a}")).json()
    assert ea["parents"][0]["name"] == "乙集团"


@pytest.mark.asyncio
async def test_self_relation_400(client):
    a = await _new_entity(client, "自己")
    r = await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": a, "counterpart_role": "parent", "relation_type": "controlling",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_relation_409(client):
    a = await _new_entity(client, "A")
    b = await _new_entity(client, "B")
    body = {"counterpart_id": b, "counterpart_role": "parent", "relation_type": "controlling"}
    assert (await client.post(f"/api/publishers/{a}/relations", json=body)).status_code == 201
    assert (await client.post(f"/api/publishers/{a}/relations", json=body)).status_code == 409


@pytest.mark.asyncio
async def test_invalid_relation_type_422(client):
    a = await _new_entity(client, "A")
    b = await _new_entity(client, "B")
    r = await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "parent", "relation_type": "bff",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_role_422(client):
    a = await _new_entity(client, "A")
    b = await _new_entity(client, "B")
    r = await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "sibling", "relation_type": "controlling",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_stake_out_of_range_422(client):
    a = await _new_entity(client, "A")
    b = await _new_entity(client, "B")
    r = await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "parent", "relation_type": "controlling", "stake_pct": 150,
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_counterpart_missing_404(client):
    a = await _new_entity(client, "A")
    r = await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": 99999, "counterpart_role": "parent", "relation_type": "controlling",
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_add_to_missing_entity_404(client):
    b = await _new_entity(client, "B")
    r = await client.post("/api/publishers/99999/relations", json={
        "counterpart_id": b, "counterpart_role": "parent", "relation_type": "controlling",
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_relation_clears_both_sides(client):
    a = await _new_entity(client, "A")
    b = await _new_entity(client, "B")
    link = (await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "parent", "relation_type": "controlling",
    })).json()
    rid = link["relation_id"]
    # 从对侧 B 删也应生效（关系涉及 B）
    assert (await client.delete(f"/api/publishers/{b}/relations/{rid}")).status_code == 200
    assert (await client.get(f"/api/publishers/{a}")).json()["parents"] == []
    assert (await client.get(f"/api/publishers/{b}")).json()["children"] == []


@pytest.mark.asyncio
async def test_delete_entity_removes_its_relations(client):
    a = await _new_entity(client, "A")
    b = await _new_entity(client, "母公司B")
    await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "parent", "relation_type": "controlling",
    })
    # 删母公司 B → A 的 parents 应清空（关系随之删除，不留孤儿）
    assert (await client.delete(f"/api/publishers/{b}")).status_code == 200
    assert (await client.get(f"/api/publishers/{a}")).json()["parents"] == []


@pytest.mark.asyncio
async def test_relations_in_list_view(client):
    a = await _new_entity(client, "列表子")
    b = await _new_entity(client, "列表母")
    await client.post(f"/api/publishers/{a}/relations", json={
        "counterpart_id": b, "counterpart_role": "parent", "relation_type": "controlling", "stake_pct": 51,
    })
    lst = (await client.get("/api/publishers/")).json()
    ea = next(x for x in lst if x["name"] == "列表子")
    eb = next(x for x in lst if x["name"] == "列表母")
    assert ea["parents"][0]["name"] == "列表母"
    assert eb["children"][0]["name"] == "列表子"
    assert eb["children"][0]["stake_pct"] == pytest.approx(51)
