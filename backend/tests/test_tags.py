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
