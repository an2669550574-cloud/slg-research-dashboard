"""自有产品素材：上传/文字/列表/删除 + 站内预览（令牌+Range）+ 删产品级联。

CJK 硬规则：素材/上传/文件流类功能必须用中文测试数据（中文标题/文件名），
回归 Content-Disposition latin-1 编码 500 那类真 bug。

analyze 端点要真打 LLM 网关，不在单测覆盖（无 key 会 4xx/5xx）；这里只覆盖
不依赖外部网关的 CRUD + 文件流 + 级联。
"""
import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _media_tmp(monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))


async def _make_product(client, name="《极寒纪元》"):
    r = await client.post("/api/products/", json={
        "name": name, "brief": "末日生存 SLG", "is_default": True})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_add_text_material_cjk(client):
    pid = await _make_product(client)
    r = await client.post(f"/api/products/{pid}/materials/text", json={
        "title": "App Store 描述", "text_content": "极寒末世题材，城建+联盟国战，温度生存机制。"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["asset_type"] == "text"
    assert b["title"] == "App Store 描述"
    assert "温度生存" in b["text_content"]
    assert b["preview_url"] is None  # 文字素材无文件


@pytest.mark.asyncio
async def test_upload_image_and_list(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    pid = await _make_product(client)
    up = await client.post(
        f"/api/products/{pid}/materials/upload",
        data={"title": "首屏截图"},
        files={"file": ("商店截图.png", PNG, "image/png")},
    )
    assert up.status_code == 201, up.text
    b = up.json()
    assert b["asset_type"] == "image"
    assert b["file_name"] == "商店截图.png"
    assert b["preview_url"] and f"/api/products/materials/{b['id']}/file?token=" in b["preview_url"]

    rows = (await client.get(f"/api/products/{pid}/materials")).json()
    assert len(rows) == 1
    assert rows[0]["asset_type"] == "image"


@pytest.mark.asyncio
async def test_serve_product_file_cjk_and_range(client, monkeypatch, tmp_path):
    """中文文件名预览不得 500；Range 拖拽 206。"""
    _media_tmp(monkeypatch, tmp_path)
    pid = await _make_product(client)
    up = (await client.post(
        f"/api/products/{pid}/materials/upload",
        data={"title": "宣传片"},
        files={"file": ("宣传片-终版.mp4", b"0123456789", "video/mp4")},
    )).json()
    mid = up["id"]

    full = await client.get(f"/api/products/materials/{mid}/file")
    assert full.status_code == 200, full.text
    assert full.content == b"0123456789"
    cd = full.headers.get("content-disposition", "")
    cd.encode("latin-1")  # 合法响应头，不抛
    assert "filename*=UTF-8''" in cd

    part = await client.get(f"/api/products/materials/{mid}/file", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"

    # 配 API_KEY 后令牌强制
    from app.config import settings
    from app.services import media
    monkeypatch.setattr(settings, "API_KEY", "secret-key")
    assert (await client.get(f"/api/products/materials/{mid}/file")).status_code == 403
    good = media.sign(mid)
    assert (await client.get(f"/api/products/materials/{mid}/file?token={good}")).status_code == 200


@pytest.mark.asyncio
async def test_delete_material_unlinks_file(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    pid = await _make_product(client)
    up = (await client.post(
        f"/api/products/{pid}/materials/upload",
        data={"title": "图"},
        files={"file": ("图.png", PNG, "image/png")},
    )).json()
    media_dir = tmp_path / "media"
    assert len(list(media_dir.iterdir())) == 1
    await client.delete(f"/api/products/{pid}/materials/{up['id']}")
    assert list(media_dir.iterdir()) == [], "删素材应连带删档"


@pytest.mark.asyncio
async def test_delete_product_cascades_materials_and_files(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    pid = await _make_product(client)
    await client.post(f"/api/products/{pid}/materials/text", json={"text_content": "描述文字"})
    await client.post(
        f"/api/products/{pid}/materials/upload",
        data={"title": "图"},
        files={"file": ("图.png", PNG, "image/png")},
    )
    media_dir = tmp_path / "media"
    assert len(list(media_dir.iterdir())) == 1

    r = await client.delete(f"/api/products/{pid}")
    assert r.status_code == 200, r.text
    # 子素材随产品删除：列表端点对已删产品应 404
    assert (await client.get(f"/api/products/{pid}/materials")).status_code == 404
    assert list(media_dir.iterdir()) == [], "删产品应连带删子素材落盘文件"


@pytest.mark.asyncio
async def test_analyze_without_materials_400(client):
    pid = await _make_product(client)
    r = await client.post(f"/api/products/{pid}/analyze")
    assert r.status_code == 400
    assert "素材" in r.json()["detail"]
