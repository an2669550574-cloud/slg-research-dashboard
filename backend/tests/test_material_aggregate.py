"""聚合分析（P4）测试：按文字型一级标签统计去重素材分布 + 交叉透视。
中文数据（路型 / 桶子）。多选维度下一素材可计入多桶；scope 过滤先圈后聚。"""
import pytest


async def _mk_text_dim(client, name, values, material_type=None):
    dim = (await client.post("/api/tags/dimensions", json={
        "name": name, "value_type": "text", "allow_multi": True,
        "material_type": material_type,
    })).json()
    opts = {}
    for v in values:
        o = (await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": v})).json()
        opts[v] = o["id"]
    return dim, opts


async def _mk_material(client, title, tag_values, material_type="video"):
    r = await client.post("/api/materials/", json={
        "app_id": "com.test.slg", "title": title, "url": "https://e.com/a",
        "material_type": material_type, "tag_values": tag_values,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _agg(client, dimension_id, **params):
    r = await client.get("/api/tags/aggregate", params={"dimension_id": dimension_id, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _counts(agg):
    return {b["value"]: b["count"] for b in agg["buckets"]}


@pytest.mark.asyncio
async def test_aggregate_single_dim_distribution(client):
    road, ropt = await _mk_text_dim(client, "路型", ["1路", "2路", "3路"])
    one = [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}]
    two = [{"dimension_id": road["id"], "option_ids": [ropt["2路"]]}]
    both = [{"dimension_id": road["id"], "option_ids": [ropt["1路"], ropt["2路"]]}]
    await _mk_material(client, "甲", one)
    await _mk_material(client, "乙", two)
    await _mk_material(client, "丙", both)   # 多选：同时计入 1路、2路
    await _mk_material(client, "丁", [])      # 在 scope 内但本维度未打标

    agg = await _agg(client, road["id"])
    # 完整口径：含计数为 0 的 3路 桶
    assert _counts(agg) == {"1路": 2, "2路": 2, "3路": 0}
    assert agg["total_materials"] == 4   # 甲乙丙丁
    assert agg["tagged_materials"] == 3  # 丁 无 路型 值
    assert agg["by_dimension_id"] is None


@pytest.mark.asyncio
async def test_aggregate_cross_tab(client):
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

    agg = await _agg(client, road["id"], by=bucket["id"])
    assert agg["by_dimension_name"] == "桶子"
    sub = {b["value"]: {s["value"]: s["count"] for s in b["sub"]} for b in agg["buckets"]}
    assert sub["1路"] == {"红桶": 1, "蓝桶": 1}   # 红一、蓝一
    assert sub["2路"] == {"红桶": 1, "蓝桶": 0}   # 红二


@pytest.mark.asyncio
async def test_aggregate_scope_by_facet(client):
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
    # 先用分面把 scope 收到「1路」素材（红一、蓝一），再看桶子分布
    agg = await _agg(client, bucket["id"], tag_options=str(ropt["1路"]))
    assert _counts(agg) == {"红桶": 1, "蓝桶": 1}
    assert agg["total_materials"] == 2


@pytest.mark.asyncio
async def test_aggregate_scope_by_material_type(client):
    road, ropt = await _mk_text_dim(client, "路型", ["1路"])
    await _mk_material(client, "视频甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}], material_type="video")
    await _mk_material(client, "图片乙", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}], material_type="image")
    agg = await _agg(client, road["id"], material_type="video")
    assert _counts(agg) == {"1路": 1}
    assert agg["total_materials"] == 1


@pytest.mark.asyncio
async def test_aggregate_rejects_date_dim(client):
    dim = (await client.post("/api/tags/dimensions", json={
        "name": "投放时间", "value_type": "date",
    })).json()
    r = await client.get("/api/tags/aggregate", params={"dimension_id": dim["id"]})
    assert r.status_code == 400, r.text
