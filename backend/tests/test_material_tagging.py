"""结构化打标签 + 必填校验（P2）测试。中文数据（对齐需求：路型 / 投放时间 / 桶子）。"""
import pytest


async def _mk_road_dim(client, *, required=False, multi=True):
    """建「路型」text 维度 + 1路~4路；返回 (dim, {value: option_id})。"""
    dim = (await client.post("/api/tags/dimensions", json={
        "name": "路型", "value_type": "text", "is_required": required, "allow_multi": multi,
    })).json()
    opts = {}
    for v in ["1路", "2路", "3路", "4路"]:
        o = (await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": v})).json()
        opts[v] = o["id"]
    return dim, opts


async def _mk_material(client, **tag_values):
    body = {"app_id": "com.test.slg", "title": "中文素材", "url": "https://e.com/a",
            "material_type": "video"}
    if tag_values:
        body["tag_values"] = tag_values["values"]
    r = await client.post("/api/materials/", json=body)
    return r


@pytest.mark.asyncio
async def test_tag_material_text_and_date(client):
    dim, opts = await _mk_road_dim(client)
    date_dim = (await client.post("/api/tags/dimensions", json={
        "name": "投放时间", "value_type": "date", "allow_multi": False,
    })).json()
    m = (await _mk_material(client)).json()

    r = await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim["id"], "option_ids": [opts["3路"], opts["1路"]]},
        {"dimension_id": date_dim["id"], "value_date": "2026-05-20"},
    ]})
    assert r.status_code == 200, r.text
    tv = r.json()["tag_values"]
    road_vals = sorted(x["value"] for x in tv if x["dimension_id"] == dim["id"])
    assert road_vals == ["1路", "3路"]
    date_item = next(x for x in tv if x["dimension_id"] == date_dim["id"])
    assert date_item["value_date"] == "2026-05-20"

    # 列表 / 单读都带 tag_values
    got = (await client.get(f"/api/materials/{m['id']}")).json()
    assert len(got["tag_values"]) == 3


@pytest.mark.asyncio
async def test_required_blocks_create_without_tag(client):
    # 必填「投放时间」存在 → 不带 tag_values 建素材应 400
    await client.post("/api/tags/dimensions", json={
        "name": "投放时间", "value_type": "date", "is_required": True, "allow_multi": False,
    })
    r = await _mk_material(client)
    assert r.status_code == 400 and "必填" in r.json()["detail"]


@pytest.mark.asyncio
async def test_required_satisfied_inline_on_create(client):
    d = (await client.post("/api/tags/dimensions", json={
        "name": "投放时间", "value_type": "date", "is_required": True, "allow_multi": False,
    })).json()
    r = await _mk_material(client, values=[{"dimension_id": d["id"], "value_date": "2026-05-20"}])
    assert r.status_code == 201, r.text
    assert r.json()["tag_values"][0]["value_date"] == "2026-05-20"


@pytest.mark.asyncio
async def test_single_select_rejects_multiple(client):
    dim, opts = await _mk_road_dim(client, multi=False)
    m = (await _mk_material(client)).json()
    r = await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim["id"], "option_ids": [opts["1路"], opts["2路"]]},
    ]})
    assert r.status_code == 400 and "单选" in r.json()["detail"]


@pytest.mark.asyncio
async def test_option_must_belong_to_dimension(client):
    dim_a, opts_a = await _mk_road_dim(client)
    dim_b = (await client.post("/api/tags/dimensions", json={"name": "桶子", "value_type": "text"})).json()
    m = (await _mk_material(client)).json()
    # 把路型的 option 塞给桶子维度 → 400
    r = await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim_b["id"], "option_ids": [opts_a["1路"]]},
    ]})
    assert r.status_code == 400 and "不属于" in r.json()["detail"]


@pytest.mark.asyncio
async def test_put_replace_all(client):
    dim, opts = await _mk_road_dim(client)
    m = (await _mk_material(client)).json()
    await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim["id"], "option_ids": [opts["1路"], opts["2路"]]},
    ]})
    # 二次 PUT 覆盖为 3路
    r = await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim["id"], "option_ids": [opts["3路"]]},
    ]})
    vals = sorted(x["value"] for x in r.json()["tag_values"])
    assert vals == ["3路"]


@pytest.mark.asyncio
async def test_rename_option_syncs_material_value(client):
    dim, opts = await _mk_road_dim(client)
    m = (await _mk_material(client)).json()
    await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim["id"], "option_ids": [opts["3路"]]},
    ]})
    # 把「3路」改名 → 素材上冗余 value 同步刷新
    await client.put(f"/api/tags/options/{opts['3路']}", json={"value": "三路"})
    got = (await client.get(f"/api/materials/{m['id']}")).json()
    assert got["tag_values"][0]["value"] == "三路"


@pytest.mark.asyncio
async def test_delete_dimension_clears_material_tags(client):
    dim, opts = await _mk_road_dim(client)
    m = (await _mk_material(client)).json()
    await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim["id"], "option_ids": [opts["1路"]]},
    ]})
    d = await client.delete(f"/api/tags/dimensions/{dim['id']}")
    assert d.json()["removed_material_tags"] == 1
    got = (await client.get(f"/api/materials/{m['id']}")).json()
    assert got["tag_values"] == []


@pytest.mark.asyncio
async def test_delete_material_clears_tag_rows(client):
    """删素材必须显式清 material_tag_values——SQLite foreign_keys=OFF（#232 刻意），
    DDL 的 ondelete=CASCADE 不生效，不显式删就留孤儿（prod material_id=5 实锤）。"""
    from sqlalchemy import select, func
    from app.models.tag import MaterialTagValue
    from app.database import AsyncSessionLocal

    dim, opts = await _mk_road_dim(client)
    m = (await _mk_material(client)).json()
    await client.put(f"/api/materials/{m['id']}/tag-values", json={"values": [
        {"dimension_id": dim["id"], "option_ids": [opts["1路"], opts["2路"]]},
    ]})

    r = await client.delete(f"/api/materials/{m['id']}")
    assert r.status_code == 200

    async with AsyncSessionLocal() as db:
        n = (await db.execute(
            select(func.count()).select_from(MaterialTagValue)
            .where(MaterialTagValue.material_id == m["id"])
        )).scalar()
    assert n == 0, f"删素材后残留 {n} 行孤儿标签"
