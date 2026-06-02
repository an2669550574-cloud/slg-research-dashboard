"""结构化分面筛选（P3）测试：同维度内 OR、跨维度 AND。中文数据（路型 / 桶子）。"""
import pytest


async def _mk_text_dim(client, name, values):
    dim = (await client.post("/api/tags/dimensions", json={
        "name": name, "value_type": "text", "allow_multi": True,
    })).json()
    opts = {}
    for v in values:
        o = (await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": v})).json()
        opts[v] = o["id"]
    return dim, opts


async def _mk_material(client, title, tag_values):
    r = await client.post("/api/materials/", json={
        "app_id": "com.test.slg", "title": title, "url": "https://e.com/a",
        "material_type": "video", "tag_values": tag_values,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _titles(client, tag_options):
    r = await client.get("/api/materials/", params={"tag_options": tag_options})
    assert r.status_code == 200, r.text
    return sorted(m["title"] for m in r.json())


@pytest.mark.asyncio
async def test_facet_single_dim_or(client):
    road, ropt = await _mk_text_dim(client, "路型", ["1路", "2路", "3路"])
    await _mk_material(client, "甲-一路", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    await _mk_material(client, "乙-二路", [{"dimension_id": road["id"], "option_ids": [ropt["2路"]]}])
    await _mk_material(client, "丙-三路", [{"dimension_id": road["id"], "option_ids": [ropt["3路"]]}])

    # 同维度内多选 → OR
    assert await _titles(client, f"{ropt['1路']},{ropt['2路']}") == ["乙-二路", "甲-一路"]


@pytest.mark.asyncio
async def test_facet_cross_dim_and(client):
    road, ropt = await _mk_text_dim(client, "路型", ["1路", "2路"])
    bucket, bopt = await _mk_text_dim(client, "桶子", ["红桶", "蓝桶"])
    await _mk_material(client, "红一", [
        {"dimension_id": road["id"], "option_ids": [ropt["1路"]]},
        {"dimension_id": bucket["id"], "option_ids": [bopt["红桶"]]},
    ])
    await _mk_material(client, "蓝一", [
        {"dimension_id": road["id"], "option_ids": [ropt["1路"]]},
        {"dimension_id": bucket["id"], "option_ids": [bopt["蓝桶"]]},
    ])
    await _mk_material(client, "红二", [
        {"dimension_id": road["id"], "option_ids": [ropt["2路"]]},
        {"dimension_id": bucket["id"], "option_ids": [bopt["红桶"]]},
    ])

    # 跨维度 → AND（1路 且 红桶）：只剩「红一」
    assert await _titles(client, f"{ropt['1路']},{bopt['红桶']}") == ["红一"]
    # 同维度 OR 与跨维度 AND 组合：(1路 或 2路) 且 红桶 → 红一、红二
    assert await _titles(client, f"{ropt['1路']},{ropt['2路']},{bopt['红桶']}") == ["红一", "红二"]


@pytest.mark.asyncio
async def test_facet_empty_and_garbage_ignored(client):
    road, ropt = await _mk_text_dim(client, "路型", ["1路"])
    await _mk_material(client, "甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    # 脏值/空段被解析守卫忽略，不 500；解析后无有效 id → 不加筛选，返回全部
    assert await _titles(client, "") == ["甲"]
    assert await _titles(client, "abc, ,") == ["甲"]
