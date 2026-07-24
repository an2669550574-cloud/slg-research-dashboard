"""标签包（tag pack）测试。中文数据（对齐项目 CJK 验证惯例：物资链路 / 投放要点）。"""
import pytest


async def _mk_dim(client, name: str, **kw):
    r = await client.post("/api/tags/dimensions", json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_create_pack_with_members_and_list(client):
    d1 = await _mk_dim(client, "一级物资")
    d2 = await _mk_dim(client, "二级物资")
    r = await client.post("/api/tags/packs", json={
        "name": "物资链路", "dimension_ids": [d1["id"], d2["id"]],
    })
    assert r.status_code == 201, r.text
    pack = r.json()
    assert pack["name"] == "物资链路"
    assert pack["dimension_ids"] == [d1["id"], d2["id"]]
    assert pack["app_ids"] == []

    lst = (await client.get("/api/tags/packs")).json()
    assert [p["name"] for p in lst] == ["物资链路"]


@pytest.mark.asyncio
async def test_pack_name_unique_and_length(client):
    ok = await client.post("/api/tags/packs", json={"name": "投放要点"})
    assert ok.status_code == 201
    dup = await client.post("/api/tags/packs", json={"name": "投放要点"})
    assert dup.status_code == 409
    # 20 字上限：20 过 / 21 拒（提交时校验）
    assert (await client.post("/api/tags/packs", json={"name": "一" * 20})).status_code == 201
    assert (await client.post("/api/tags/packs", json={"name": "一" * 21})).status_code == 422


@pytest.mark.asyncio
async def test_pack_member_replace_all_and_missing_dim(client):
    d1 = await _mk_dim(client, "第一步")
    d2 = await _mk_dim(client, "第二步")
    pack = (await client.post("/api/tags/packs", json={
        "name": "玩法步骤", "dimension_ids": [d1["id"]],
    })).json()
    # replace-all：换成 [d2]
    r = await client.put(f"/api/tags/packs/{pack['id']}", json={"dimension_ids": [d2["id"]]})
    assert r.status_code == 200 and r.json()["dimension_ids"] == [d2["id"]]
    # [] = 清空成员（允许空包）
    r = await client.put(f"/api/tags/packs/{pack['id']}", json={"dimension_ids": []})
    assert r.status_code == 200 and r.json()["dimension_ids"] == []
    # 不存在的维度 → 404 整体回滚
    r = await client.put(f"/api/tags/packs/{pack['id']}", json={"dimension_ids": [999999]})
    assert r.status_code == 404
    # 成员去重：同 id 传两次只存一份
    r = await client.put(f"/api/tags/packs/{pack['id']}",
                         json={"dimension_ids": [d1["id"], d1["id"]]})
    assert r.status_code == 200 and r.json()["dimension_ids"] == [d1["id"]]


@pytest.mark.asyncio
async def test_pack_rename_and_scope(client):
    pack = (await client.post("/api/tags/packs", json={"name": "临时包"})).json()
    r = await client.put(f"/api/tags/packs/{pack['id']}",
                         json={"name": "素材要素", "app_ids": ["alpha", "beta"]})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "素材要素" and sorted(body["app_ids"]) == ["alpha", "beta"]
    # [] = 改回通用
    r = await client.put(f"/api/tags/packs/{pack['id']}", json={"app_ids": []})
    assert r.status_code == 200 and r.json()["app_ids"] == []


@pytest.mark.asyncio
async def test_pack_scope_filter_by_app_id(client):
    universal = (await client.post("/api/tags/packs", json={"name": "通用包"})).json()
    scoped = (await client.post("/api/tags/packs", json={
        "name": "甲方专属", "app_ids": ["alpha"],
    })).json()
    # 浏览态：alpha 看到两个；beta 只看到通用
    for_alpha = (await client.get("/api/tags/packs", params={"app_id": "alpha"})).json()
    assert {p["id"] for p in for_alpha} == {universal["id"], scoped["id"]}
    for_beta = (await client.get("/api/tags/packs", params={"app_id": "beta"})).json()
    assert {p["id"] for p in for_beta} == {universal["id"]}
    # 管理态（不传 app_id）：全量 + app_ids 名单可见
    all_packs = (await client.get("/api/tags/packs")).json()
    assert {p["id"] for p in all_packs} == {universal["id"], scoped["id"]}


@pytest.mark.asyncio
async def test_pack_reorder(client):
    a = (await client.post("/api/tags/packs", json={"name": "包甲"})).json()
    b = (await client.post("/api/tags/packs", json={"name": "包乙"})).json()
    c = (await client.post("/api/tags/packs", json={"name": "包丙"})).json()
    r = await client.put("/api/tags/packs/reorder",
                         json={"ordered_ids": [c["id"], a["id"], b["id"]]})
    assert r.status_code == 200 and r.json()["reordered"] == 3
    lst = (await client.get("/api/tags/packs")).json()
    assert [p["name"] for p in lst] == ["包丙", "包甲", "包乙"]
    # 不存在的 id → 404
    r = await client.put("/api/tags/packs/reorder", json={"ordered_ids": [999999]})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_dimension_removes_pack_membership(client):
    d = await _mk_dim(client, "货币")
    pack = (await client.post("/api/tags/packs", json={
        "name": "经济系统", "dimension_ids": [d["id"]],
    })).json()
    # 删维度（口令未配置 → 放行）→ 包保留但成员被摘除
    assert (await client.delete(f"/api/tags/dimensions/{d['id']}")).status_code == 200
    lst = (await client.get("/api/tags/packs")).json()
    survivor = next(p for p in lst if p["id"] == pack["id"])
    assert survivor["dimension_ids"] == []


@pytest.mark.asyncio
async def test_delete_pack_keeps_dimensions(client):
    d = await _mk_dim(client, "最终结果")
    pack = (await client.post("/api/tags/packs", json={
        "name": "结局归类", "dimension_ids": [d["id"]],
    })).json()
    r = await client.delete(f"/api/tags/packs/{pack['id']}")
    assert r.status_code == 200 and r.json()["removed_members"] == 1
    # 维度毫发无损（删包只删分组配置）
    dims = (await client.get("/api/tags/dimensions")).json()
    assert any(x["id"] == d["id"] for x in dims)
    assert (await client.get("/api/tags/packs")).json() == []


@pytest.mark.asyncio
async def test_pack_setting_default_off_and_upsert(client):
    # 无记录 = 默认关
    r = await client.get("/api/tags/packs/settings/com.example.战争游戏")
    assert r.status_code == 200 and r.json()["enabled"] is False
    # 开
    r = await client.put("/api/tags/packs/settings/com.example.战争游戏", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    r = await client.get("/api/tags/packs/settings/com.example.战争游戏")
    assert r.json()["enabled"] is True
    # 再关（upsert 更新既有行）
    r = await client.put("/api/tags/packs/settings/com.example.战争游戏", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False


# ── 「仅看已打标」has_dimensions 筛选（切片 2）────────────────────────────

async def _mk_material_titled(client, title: str):
    r = await client.post("/api/materials/", json={
        "app_id": "com.test.slg", "title": title,
        "url": f"https://e.com/{title}", "material_type": "video",
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _mk_dim_with_opt(client, dim_name: str, opt_value: str):
    dim = (await client.post("/api/tags/dimensions", json={"name": dim_name})).json()
    opt = (await client.post(f"/api/tags/dimensions/{dim['id']}/options",
                             json={"value": opt_value})).json()
    return dim, opt


@pytest.mark.asyncio
async def test_has_dimensions_filter(client):
    """素材在给定维度中至少一个有已打标记才命中（维度间 OR）。"""
    d1, o1 = await _mk_dim_with_opt(client, "一级物资", "木头")
    d2, o2 = await _mk_dim_with_opt(client, "二级物资", "鱼肉")
    tagged1 = await _mk_material_titled(client, "打了物资标的素材")
    tagged2 = await _mk_material_titled(client, "打了二级标的素材")
    untagged = await _mk_material_titled(client, "白板素材")
    await client.put(f"/api/materials/{tagged1['id']}/tag-values", json={"values": [
        {"dimension_id": d1["id"], "option_ids": [o1["id"]]},
    ]})
    await client.put(f"/api/materials/{tagged2['id']}/tag-values", json={"values": [
        {"dimension_id": d2["id"], "option_ids": [o2["id"]]},
    ]})

    # 单维度：只命中 tagged1
    got = (await client.get("/api/materials/", params={"has_dimensions": str(d1["id"])})).json()
    assert {m["id"] for m in got} == {tagged1["id"]}
    # 两个维度 OR：命中 tagged1 + tagged2，白板不进
    got = (await client.get("/api/materials/",
                            params={"has_dimensions": f"{d1['id']},{d2['id']}"})).json()
    assert {m["id"] for m in got} == {tagged1["id"], tagged2["id"]}
    # 不传 = 不过滤
    got = (await client.get("/api/materials/")).json()
    assert {m["id"] for m in got} >= {tagged1["id"], tagged2["id"], untagged["id"]}
    # 脏值静默忽略（不 500、不过滤）
    got = (await client.get("/api/materials/", params={"has_dimensions": "abc,,"})).json()
    assert {m["id"] for m in got} >= {untagged["id"]}


@pytest.mark.asyncio
async def test_has_dimensions_stacks_with_tag_options(client):
    """与 tag_options 分面叠加为 AND：都满足才命中。"""
    d1, o1 = await _mk_dim_with_opt(client, "一级物资", "木头")
    d2, o2 = await _mk_dim_with_opt(client, "第一步", "打熊")
    both = await _mk_material_titled(client, "两维都打")
    only_d2 = await _mk_material_titled(client, "只打第一步")
    await client.put(f"/api/materials/{both['id']}/tag-values", json={"values": [
        {"dimension_id": d1["id"], "option_ids": [o1["id"]]},
        {"dimension_id": d2["id"], "option_ids": [o2["id"]]},
    ]})
    await client.put(f"/api/materials/{only_d2['id']}/tag-values", json={"values": [
        {"dimension_id": d2["id"], "option_ids": [o2["id"]]},
    ]})
    got = (await client.get("/api/materials/", params={
        "tag_options": str(o2["id"]),          # 分面选中「打熊」
        "has_dimensions": str(d1["id"]),        # 且在「一级物资」有打标
    })).json()
    assert {m["id"] for m in got} == {both["id"]}


# ── 选项子集成员（0047）──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pack_option_subset_members(client):
    """包成员可细化到二级标签；输出 option_ids；不存在的选项 404。"""
    d, o1 = await _mk_dim_with_opt(client, "货币种类", "金币")
    o2 = (await client.post(f"/api/tags/dimensions/{d['id']}/options",
                            json={"value": "钞票"})).json()
    pack = (await client.post("/api/tags/packs", json={
        "name": "经济要素", "option_ids": [o1["id"]],
    })).json()
    assert pack["dimension_ids"] == [] and pack["option_ids"] == [o1["id"]]
    # replace-all 换成两个
    r = await client.put(f"/api/tags/packs/{pack['id']}",
                         json={"option_ids": [o1["id"], o2["id"]]})
    assert r.status_code == 200 and set(r.json()["option_ids"]) == {o1["id"], o2["id"]}
    # 不存在的选项 → 404
    r = await client.put(f"/api/tags/packs/{pack['id']}", json={"option_ids": [999999]})
    assert r.status_code == 404
    # [] = 清空子集
    r = await client.put(f"/api/tags/packs/{pack['id']}", json={"option_ids": []})
    assert r.status_code == 200 and r.json()["option_ids"] == []


@pytest.mark.asyncio
async def test_pack_option_normalize_whole_dim_wins(client):
    """归一（整维度优先）：①建包时同维度既整选又给选项 → 选项被剔；
    ②后续把维度升级为整维度 → 旧选项子集自动摘除。"""
    d, o1 = await _mk_dim_with_opt(client, "视频开头", "真人")
    # ① 同请求里整维度 + 其选项 → 选项忽略
    pack = (await client.post("/api/tags/packs", json={
        "name": "开头要素", "dimension_ids": [d["id"]], "option_ids": [o1["id"]],
    })).json()
    assert pack["dimension_ids"] == [d["id"]] and pack["option_ids"] == []
    # 改成纯选项子集
    r = await client.put(f"/api/tags/packs/{pack['id']}",
                         json={"dimension_ids": [], "option_ids": [o1["id"]]})
    assert r.json()["dimension_ids"] == [] and r.json()["option_ids"] == [o1["id"]]
    # ② 升级回整维度（不动 option_ids）→ 子集被归一摘除
    r = await client.put(f"/api/tags/packs/{pack['id']}", json={"dimension_ids": [d["id"]]})
    assert r.json()["dimension_ids"] == [d["id"]] and r.json()["option_ids"] == []


@pytest.mark.asyncio
async def test_delete_option_and_dimension_clean_pack_options(client):
    """删二级标签 / 删维度都连带清包的选项子集成员；包本身保留。"""
    d, o1 = await _mk_dim_with_opt(client, "基础物资", "木头")
    o2 = (await client.post(f"/api/tags/dimensions/{d['id']}/options",
                            json={"value": "土豆"})).json()
    pack = (await client.post("/api/tags/packs", json={
        "name": "物资要素", "option_ids": [o1["id"], o2["id"]],
    })).json()
    # 删一个选项 → 只摘那一个
    assert (await client.delete(f"/api/tags/options/{o1['id']}")).status_code == 200
    lst = (await client.get("/api/tags/packs")).json()
    assert next(p for p in lst if p["id"] == pack["id"])["option_ids"] == [o2["id"]]
    # 删整个维度 → 剩余选项成员也清空
    assert (await client.delete(f"/api/tags/dimensions/{d['id']}")).status_code == 200
    lst = (await client.get("/api/tags/packs")).json()
    survivor = next(p for p in lst if p["id"] == pack["id"])
    assert survivor["option_ids"] == [] and survivor["dimension_ids"] == []
