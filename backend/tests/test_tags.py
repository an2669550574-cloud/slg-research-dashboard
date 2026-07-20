"""标签库 CRUD（P1）测试。用中文数据（对齐需求测试用例：路型 / 投放时间）。"""
import pytest


@pytest.mark.asyncio
async def test_create_text_dimension_with_options_and_list(client):
    # 文字型一级标签：路型
    r = await client.post("/api/tags/dimensions", json={"name": "路型", "value_type": "text"})
    assert r.status_code == 201, r.text
    dim = r.json()
    assert dim["value_type"] == "text" and dim["options"] == []

    for v in ["1路", "2路", "3路", "4路"]:
        ro = await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": v})
        assert ro.status_code == 201, ro.text

    # 列表带嵌套二级标签
    lst = (await client.get("/api/tags/dimensions")).json()
    road = next(d for d in lst if d["name"] == "路型")
    assert [o["value"] for o in road["options"]] == ["1路", "2路", "3路", "4路"]


@pytest.mark.asyncio
async def test_date_dimension_rejects_options(client):
    r = await client.post("/api/tags/dimensions", json={"name": "投放时间", "value_type": "date", "is_required": True})
    assert r.status_code == 201
    dim = r.json()
    # 时间型不能加二级标签（打标签时选日期）
    ro = await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": "随便"})
    assert ro.status_code == 400


@pytest.mark.asyncio
async def test_name_length_validated_on_submit(client):
    # 8 个中文字符通过
    ok = await client.post("/api/tags/dimensions", json={"name": "一二三四五六七八"})
    assert ok.status_code == 201
    # 9 个超限 → 422（提交时校验）
    bad = await client.post("/api/tags/dimensions", json={"name": "一二三四五六七八九"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_option_rejected(client):
    dim = (await client.post("/api/tags/dimensions", json={"name": "桶子"})).json()
    assert (await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": "红桶"})).status_code == 201
    dup = await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": "红桶"})
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_rename_option(client):
    dim = (await client.post("/api/tags/dimensions", json={"name": "路型"})).json()
    opt = (await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": "三路"})).json()
    r = await client.put(f"/api/tags/options/{opt['id']}", json={"value": "3路"})
    assert r.status_code == 200 and r.json()["value"] == "3路"


@pytest.mark.asyncio
async def test_delete_dimension_cascades_options(client):
    dim = (await client.post("/api/tags/dimensions", json={"name": "路型"})).json()
    await client.post(f"/api/tags/dimensions/{dim['id']}/options", json={"value": "1路"})
    # 口令未配置 → 删除放行
    d = await client.delete(f"/api/tags/dimensions/{dim['id']}")
    assert d.status_code == 200 and d.json()["removed_options"] == 1
    # 维度没了
    lst = (await client.get("/api/tags/dimensions")).json()
    assert all(x["id"] != dim["id"] for x in lst)


@pytest.mark.asyncio
async def test_dimension_product_scope_filter(client):
    """S1：维度作用域名单为空 = 通用；非空 = 仅名单内 app_id 可见。
    打标签/浏览态传 app_id 时按「无名单 OR 名单含目标」过滤。"""
    # 通用维度（不传 app_ids）
    universal = (await client.post("/api/tags/dimensions",
                                   json={"name": "路型"})).json()
    # 限定到 app=alpha 的专属维度
    scoped = (await client.post("/api/tags/dimensions",
                                json={"name": "桶子", "app_ids": ["alpha"]})).json()
    assert scoped["app_ids"] == ["alpha"]

    # 管理态（不带 app_id）→ 两个都返
    lst_all = (await client.get("/api/tags/dimensions")).json()
    names_all = {d["name"] for d in lst_all}
    assert {"路型", "桶子"} <= names_all
    # alpha → 通用 + 专属 都可见
    lst_a = (await client.get("/api/tags/dimensions?app_id=alpha")).json()
    assert {d["name"] for d in lst_a} == {"路型", "桶子"}
    # beta → 仅通用
    lst_b = (await client.get("/api/tags/dimensions?app_id=beta")).json()
    assert {d["name"] for d in lst_b} == {"路型"}


@pytest.mark.asyncio
async def test_dimension_update_app_ids_semantics(client):
    """app_ids 的三种语义：None=不动 / []=改回通用 / 非空=replace-all。"""
    d = (await client.post("/api/tags/dimensions",
                           json={"name": "心流", "app_ids": ["a", "b"]})).json()
    assert sorted(d["app_ids"]) == ["a", "b"]

    # 不传（None）→ 名单不动
    upd1 = (await client.put(f"/api/tags/dimensions/{d['id']}",
                             json={"is_required": True})).json()
    assert sorted(upd1["app_ids"]) == ["a", "b"]

    # replace-all：[a,b] → [c]
    upd2 = (await client.put(f"/api/tags/dimensions/{d['id']}",
                             json={"app_ids": ["c"]})).json()
    assert upd2["app_ids"] == ["c"]

    # [] → 改回通用
    upd3 = (await client.put(f"/api/tags/dimensions/{d['id']}",
                             json={"app_ids": []})).json()
    assert upd3["app_ids"] == []
    # 改回通用后，任意 app_id 都能看到
    lst = (await client.get("/api/tags/dimensions?app_id=anything")).json()
    assert any(x["name"] == "心流" for x in lst)


@pytest.mark.asyncio
async def test_delete_dimension_cleans_product_scope(client):
    """删维度时连带清理 tag_dimension_products（应用层级联，SQLite 不强制 FK）。"""
    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal
    from app.models.tag import TagDimensionProduct

    d = (await client.post("/api/tags/dimensions",
                           json={"name": "角色", "app_ids": ["x", "y"]})).json()
    async with AsyncSessionLocal() as db:
        before = (await db.execute(
            select(func.count()).select_from(TagDimensionProduct)
            .where(TagDimensionProduct.dimension_id == d["id"])
        )).scalar()
        assert before == 2
    assert (await client.delete(f"/api/tags/dimensions/{d['id']}")).status_code == 200
    async with AsyncSessionLocal() as db:
        after = (await db.execute(
            select(func.count()).select_from(TagDimensionProduct)
            .where(TagDimensionProduct.dimension_id == d["id"])
        )).scalar()
        assert after == 0


@pytest.mark.asyncio
async def test_option_product_scope_filter(client):
    """S2：二级标签作用域。同维度下，无名单选项对所有产品可见；有名单选项仅对名单内产品列出。
    典型场景：「角色」维度共享，A 游戏的角色值（爱丽丝/鲍勃）只对 A 显示，与 B 不混。"""
    # 维度「角色」通用（无 dim 作用域）
    role = (await client.post("/api/tags/dimensions", json={"name": "角色"})).json()
    # 三个选项：通用 / 仅 A / 仅 B
    common = (await client.post(f"/api/tags/dimensions/{role['id']}/options",
                                json={"value": "通用"})).json()
    a = (await client.post(f"/api/tags/dimensions/{role['id']}/options",
                           json={"value": "爱丽丝", "app_ids": ["A"]})).json()
    b = (await client.post(f"/api/tags/dimensions/{role['id']}/options",
                           json={"value": "鲍勃", "app_ids": ["B"]})).json()
    assert a["app_ids"] == ["A"] and b["app_ids"] == ["B"] and common["app_ids"] == []

    # A 视角：通用 + 爱丽丝
    dims_a = (await client.get("/api/tags/dimensions?app_id=A")).json()
    role_a = next(d for d in dims_a if d["name"] == "角色")
    assert {o["value"] for o in role_a["options"]} == {"通用", "爱丽丝"}

    # B 视角：通用 + 鲍勃
    dims_b = (await client.get("/api/tags/dimensions?app_id=B")).json()
    role_b = next(d for d in dims_b if d["name"] == "角色")
    assert {o["value"] for o in role_b["options"]} == {"通用", "鲍勃"}

    # 管理态：返回全部 3 个，且每个选项带 app_ids
    dims_all = (await client.get("/api/tags/dimensions")).json()
    role_all = next(d for d in dims_all if d["name"] == "角色")
    assert {o["value"] for o in role_all["options"]} == {"通用", "爱丽丝", "鲍勃"}
    by_val = {o["value"]: o["app_ids"] for o in role_all["options"]}
    assert by_val["通用"] == [] and by_val["爱丽丝"] == ["A"] and by_val["鲍勃"] == ["B"]


@pytest.mark.asyncio
async def test_option_update_app_ids_semantics(client):
    """选项 app_ids 三态：None=不动 / []=改回通用 / 非空=replace-all。"""
    dim = (await client.post("/api/tags/dimensions", json={"name": "桶子"})).json()
    o = (await client.post(f"/api/tags/dimensions/{dim['id']}/options",
                           json={"value": "红桶", "app_ids": ["a", "b"]})).json()
    assert sorted(o["app_ids"]) == ["a", "b"]

    upd1 = (await client.put(f"/api/tags/options/{o['id']}",
                             json={"sort_order": 1})).json()
    assert sorted(upd1["app_ids"]) == ["a", "b"]  # 不动

    upd2 = (await client.put(f"/api/tags/options/{o['id']}",
                             json={"app_ids": ["c"]})).json()
    assert upd2["app_ids"] == ["c"]  # replace-all

    upd3 = (await client.put(f"/api/tags/options/{o['id']}",
                             json={"app_ids": []})).json()
    assert upd3["app_ids"] == []  # 改回通用


@pytest.mark.asyncio
async def test_delete_option_cleans_product_scope(client):
    """删选项时清理 tag_option_products。"""
    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal
    from app.models.tag import TagOptionProduct

    dim = (await client.post("/api/tags/dimensions", json={"name": "心流"})).json()
    o = (await client.post(f"/api/tags/dimensions/{dim['id']}/options",
                           json={"value": "高峰", "app_ids": ["x", "y"]})).json()
    async with AsyncSessionLocal() as db:
        before = (await db.execute(
            select(func.count()).select_from(TagOptionProduct)
            .where(TagOptionProduct.option_id == o["id"])
        )).scalar()
        assert before == 2
    assert (await client.delete(f"/api/tags/options/{o['id']}")).status_code == 200
    async with AsyncSessionLocal() as db:
        after = (await db.execute(
            select(func.count()).select_from(TagOptionProduct)
        )).scalar()
        assert after == 0


@pytest.mark.asyncio
async def test_delete_dimension_cleans_option_scope(client):
    """删维度时连带清理其选项的 tag_option_products（应用层级联）。"""
    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal
    from app.models.tag import TagOptionProduct

    dim = (await client.post("/api/tags/dimensions", json={"name": "路型"})).json()
    await client.post(f"/api/tags/dimensions/{dim['id']}/options",
                      json={"value": "1路", "app_ids": ["a"]})
    await client.post(f"/api/tags/dimensions/{dim['id']}/options",
                      json={"value": "2路", "app_ids": ["b", "c"]})
    assert (await client.delete(f"/api/tags/dimensions/{dim['id']}")).status_code == 200
    async with AsyncSessionLocal() as db:
        after = (await db.execute(
            select(func.count()).select_from(TagOptionProduct)
        )).scalar()
        assert after == 0


@pytest.mark.asyncio
async def test_aggregate_buckets_respect_option_scope(client):
    """S3：聚合分析的桶按产品作用域收敛——名单外选项不出现在桶里，
    口径与 Materials 分面栏 + 打标签编辑器一致。"""
    # 维度「角色」通用，三个选项：通用 / 仅 A / 仅 B
    role = (await client.post("/api/tags/dimensions", json={"name": "角色"})).json()
    await client.post(f"/api/tags/dimensions/{role['id']}/options", json={"value": "通用"})
    await client.post(f"/api/tags/dimensions/{role['id']}/options",
                      json={"value": "爱丽丝", "app_ids": ["A"]})
    await client.post(f"/api/tags/dimensions/{role['id']}/options",
                      json={"value": "鲍勃", "app_ids": ["B"]})

    # 无 app_id → 全部 3 个桶
    res_all = (await client.get(f"/api/tags/aggregate?dimension_id={role['id']}")).json()
    assert {b["value"] for b in res_all["buckets"]} == {"通用", "爱丽丝", "鲍勃"}

    # app_id=A → 通用 + 爱丽丝
    res_a = (await client.get(f"/api/tags/aggregate?dimension_id={role['id']}&app_id=A")).json()
    assert {b["value"] for b in res_a["buckets"]} == {"通用", "爱丽丝"}

    # app_id=B → 通用 + 鲍勃
    res_b = (await client.get(f"/api/tags/aggregate?dimension_id={role['id']}&app_id=B")).json()
    assert {b["value"] for b in res_b["buckets"]} == {"通用", "鲍勃"}


@pytest.mark.asyncio
async def test_scope_batch_updates_dimensions_and_options(client):
    """S4：产品视角批量改作用域——一次提交多条维度+选项的 replace-all。"""
    role = (await client.post("/api/tags/dimensions", json={"name": "角色"})).json()
    bucket = (await client.post("/api/tags/dimensions", json={"name": "桶子"})).json()
    o1 = (await client.post(f"/api/tags/dimensions/{role['id']}/options",
                            json={"value": "爱丽丝"})).json()
    o2 = (await client.post(f"/api/tags/dimensions/{bucket['id']}/options",
                            json={"value": "红桶", "app_ids": ["X", "Y"]})).json()

    # 一次提交：维度「角色」限定 X 专属；选项「红桶」改回通用
    res = await client.put("/api/tags/scope/batch", json={
        "dimensions": [{"id": role["id"], "app_ids": ["X"]}],
        "options": [{"id": o2["id"], "app_ids": []}],
    })
    assert res.status_code == 200, res.text
    assert res.json() == {"updated_dimensions": 1, "updated_options": 1}

    lst = (await client.get("/api/tags/dimensions")).json()
    role_after = next(d for d in lst if d["id"] == role["id"])
    assert role_after["app_ids"] == ["X"]
    bucket_after = next(d for d in lst if d["id"] == bucket["id"])
    red = next(o for o in bucket_after["options"] if o["id"] == o2["id"])
    assert red["app_ids"] == []  # 已改回通用
    # o1 未在提交里 → 不动
    alice = next(o for o in role_after["options"] if o["id"] == o1["id"])
    assert alice["app_ids"] == []


@pytest.mark.asyncio
async def test_scope_batch_404_rolls_back(client):
    """任一 id 不存在 → 404 整体回滚，已提交的改动不落库。"""
    role = (await client.post("/api/tags/dimensions", json={"name": "角色"})).json()
    res = await client.put("/api/tags/scope/batch", json={
        "dimensions": [{"id": role["id"], "app_ids": ["X"]}, {"id": 999999, "app_ids": ["Z"]}],
    })
    assert res.status_code == 404
    # 回滚：role 仍是通用
    lst = (await client.get("/api/tags/dimensions")).json()
    role_after = next(d for d in lst if d["id"] == role["id"])
    assert role_after["app_ids"] == []


@pytest.mark.asyncio
async def test_admin_password_gates_delete(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ADMIN_DELETE_PASSWORD", "s3cret")
    dim = (await client.post("/api/tags/dimensions", json={"name": "路型"})).json()
    # 不带口令 → 403
    assert (await client.delete(f"/api/tags/dimensions/{dim['id']}")).status_code == 403
    # 带错口令 → 403
    assert (await client.delete(f"/api/tags/dimensions/{dim['id']}",
                                headers={"X-Admin-Password": "wrong"})).status_code == 403
    # 带对口令 → 200
    ok = await client.delete(f"/api/tags/dimensions/{dim['id']}",
                             headers={"X-Admin-Password": "s3cret"})
    assert ok.status_code == 200


# ── 模板复制（P1，/copy-template）──────────────────────────────────────────

async def _mk_scoped_dim(client, name, app_ids, *, options=None, value_type="text"):
    d = (await client.post("/api/tags/dimensions", json={
        "name": name, "value_type": value_type, "app_ids": app_ids,
    })).json()
    for v in options or []:
        await client.post(f"/api/tags/dimensions/{d['id']}/options", json={"value": v})
    return d


@pytest.mark.asyncio
async def test_copy_template_clones_scoped_dims_with_options(client):
    """克隆语义：源专属维度+选项复制给目标；两边独立（改目标不动源）。中文数据。"""
    await _mk_scoped_dim(client, "桶子", ["com.src.game"], options=["木桶", "金像（泥像）"])
    await _mk_scoped_dim(client, "投放时间", ["com.src.game"], value_type="date")

    r = await client.post("/api/tags/copy-template", json={
        "source_app_id": "com.src.game", "target_app_id": "com.tgt.game",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["copied"]) == ["投放时间", "桶子"]
    assert body["options_copied"] == 2

    # 目标视角能看到克隆（且选项齐）；源维度未被动
    tgt_dims = (await client.get("/api/tags/dimensions", params={"app_id": "com.tgt.game"})).json()
    tgt_bucket = next(d for d in tgt_dims if d["name"] == "桶子")
    assert sorted(o["value"] for o in tgt_bucket["options"]) == ["木桶", "金像（泥像）"]
    assert tgt_bucket["app_ids"] == ["com.tgt.game"]
    # 独立演进：给目标克隆加选项，源不受影响
    await client.post(f"/api/tags/dimensions/{tgt_bucket['id']}/options", json={"value": "新桶"})
    src_dims = (await client.get("/api/tags/dimensions", params={"app_id": "com.src.game"})).json()
    src_bucket = next(d for d in src_dims if d["name"] == "桶子")
    assert all(o["value"] != "新桶" for o in src_bucket["options"])


@pytest.mark.asyncio
async def test_copy_template_idempotent_and_skips_generic(client):
    """幂等：重复复制全部 skip；通用维度不复制（目标本就可见，复制=双份）。"""
    await _mk_scoped_dim(client, "路型", ["com.src.game"], options=["3路"])
    await _mk_scoped_dim(client, "通用面", [])  # 空作用域=通用

    r1 = (await client.post("/api/tags/copy-template", json={
        "source_app_id": "com.src.game", "target_app_id": "com.tgt.game",
    })).json()
    assert r1["copied"] == ["路型"] and "通用面" not in r1["copied"]

    r2 = (await client.post("/api/tags/copy-template", json={
        "source_app_id": "com.src.game", "target_app_id": "com.tgt.game",
    })).json()
    assert r2["copied"] == [] and r2["skipped"] == ["路型"]
    # 目标视角只有一份路型
    tgt_dims = (await client.get("/api/tags/dimensions", params={"app_id": "com.tgt.game"})).json()
    assert sum(1 for d in tgt_dims if d["name"] == "路型") == 1


@pytest.mark.asyncio
async def test_copy_template_validation(client):
    r = await client.post("/api/tags/copy-template", json={
        "source_app_id": "com.same", "target_app_id": "com.same",
    })
    assert r.status_code == 400
    r = await client.post("/api/tags/copy-template", json={
        "source_app_id": "com.no.dims", "target_app_id": "com.tgt",
    })
    assert r.status_code == 404
