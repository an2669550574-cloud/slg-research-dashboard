"""素材上传 + 站内播放（令牌鉴权 + Range）。conftest 重载 app.* → import 内置。

测试默认 API_KEY=""（媒体令牌放行，等同 require_api_key 开发跳过）；
令牌强制用例里临时 monkeypatch settings.API_KEY 再签发校验。
"""
import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # 够长、后缀决定类型即可


def _media_tmp(monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))


@pytest.mark.asyncio
async def test_upload_video_ok(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    r = await client.post(
        "/api/materials/upload",
        data={"title": "Promo A", "app_id": "g1", "material_type": "video",
              "platform": "tiktok", "tags": "ua, q4", "notes": "best"},
        files={"file": ("promo.mp4", b"\x00\x01fake-mp4-bytes", "video/mp4")},
    )
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["source"] == "upload"
    assert b["file_name"] == "promo.mp4"
    assert b["file_size"] == 16  # 2 + len("fake-mp4-bytes")
    assert b["mime_type"] == "video/mp4"
    assert b["url"] is None
    assert b["stream_url"] and f"/api/materials/{b['id']}/file?token=" in b["stream_url"]
    assert b["tags"] == ["ua", "q4"]


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_ext(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    r = await client.post(
        "/api/materials/upload",
        data={"title": "x", "material_type": "video"},
        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "不支持的文件类型" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_oversize(client, monkeypatch, tmp_path):
    from app.config import settings
    _media_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "MEDIA_MAX_BYTES", 8)  # 8 字节上限
    r = await client.post(
        "/api/materials/upload",
        data={"title": "big", "material_type": "video"},
        files={"file": ("big.mp4", b"0123456789ABCDEF", "video/mp4")},
    )
    assert r.status_code == 413
    # 半截文件应已删除：媒体目录无残留
    media_dir = tmp_path / "media"
    assert not media_dir.exists() or not any(media_dir.iterdir())


@pytest.mark.asyncio
async def test_list_marks_stream_url_for_upload_only(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    await client.post("/api/materials/", json={
        "app_id": "g1", "title": "ext", "url": "https://x/y", "material_type": "video"})
    await client.post(
        "/api/materials/upload",
        data={"title": "up", "app_id": "g1", "material_type": "image"},
        files={"file": ("a.png", PNG, "image/png")},
    )
    rows = (await client.get("/api/materials/", params={"app_id": "g1"})).json()
    by_src = {m["source"]: m for m in rows}
    assert by_src["link"]["stream_url"] is None
    assert by_src["link"]["url"] == "https://x/y"
    assert by_src["upload"]["stream_url"] is not None
    assert by_src["upload"]["url"] is None


@pytest.mark.asyncio
async def test_serve_file_token_and_range(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    up = (await client.post(
        "/api/materials/upload",
        data={"title": "v", "app_id": "g1", "material_type": "video"},
        files={"file": ("v.mp4", b"0123456789", "video/mp4")},
    )).json()
    mid = up["id"]

    # 开发态(API_KEY="")令牌放行：直接取流 200，且声明 Range 能力
    full = await client.get(f"/api/materials/{mid}/file")
    assert full.status_code == 200
    assert full.content == b"0123456789"
    assert full.headers.get("accept-ranges") == "bytes"

    # Range 拖拽 → 206 + 部分内容
    part = await client.get(f"/api/materials/{mid}/file", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"
    assert "bytes 2-5/10" in part.headers.get("content-range", "")

    # 配置 API_KEY 后：无/错令牌 403，正确签名 200
    from app.config import settings
    from app.services import media
    monkeypatch.setattr(settings, "API_KEY", "secret-key")
    assert (await client.get(f"/api/materials/{mid}/file")).status_code == 403
    assert (await client.get(f"/api/materials/{mid}/file?token=bad")).status_code == 403
    good = media.sign(mid)
    assert (await client.get(f"/api/materials/{mid}/file?token={good}")).status_code == 200


@pytest.mark.asyncio
async def test_serve_file_non_ascii_filename(client, monkeypatch, tmp_path):
    """中文（非 ASCII）文件名：取流不能 500。

    回归：Content-Disposition 直接塞中文文件名 → uvicorn 发头 latin-1
    编码 UnicodeEncodeError → 整个 /file 500，视频永远卡 0:00 播不了。
    """
    _media_tmp(monkeypatch, tmp_path)
    up = (await client.post(
        "/api/materials/upload",
        data={"title": "v", "app_id": "g1", "material_type": "video"},
        files={"file": ("26.05.06挖矿2-越南语.mp4", b"0123456789", "video/mp4")},
    )).json()
    mid = up["id"]

    full = await client.get(f"/api/materials/{mid}/file")
    assert full.status_code == 200, full.text          # 不再 500
    assert full.content == b"0123456789"
    cd = full.headers.get("content-disposition", "")
    # ASCII 兜底里不得残留任何非 latin-1 字符；真实名走 filename*=UTF-8''
    cd.encode("latin-1")  # 不抛即说明响应头合法
    assert "filename*=UTF-8''" in cd
    assert "%E6%8C%96%E7%9F%BF" in cd  # “挖矿” 的 UTF-8 百分号编码

    part = await client.get(f"/api/materials/{mid}/file", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"


@pytest.mark.asyncio
async def test_delete_unlinks_file(client, monkeypatch, tmp_path):
    _media_tmp(monkeypatch, tmp_path)
    up = (await client.post(
        "/api/materials/upload",
        data={"title": "v", "app_id": "g1", "material_type": "video"},
        files={"file": ("v.mp4", b"abc", "video/mp4")},
    )).json()
    media_dir = tmp_path / "media"
    assert len(list(media_dir.iterdir())) == 1, "上传后应有 1 个落盘文件"
    await client.delete(f"/api/materials/{up['id']}")
    assert list(media_dir.iterdir()) == [], "删除素材应连带删档"


def test_media_signing_secret_decoupled_from_api_key(monkeypatch):
    """媒体签名密钥独立于 API_KEY：配 MEDIA_SIGNING_SECRET 后，用 bundle 里泄露的
    API_KEY 伪造的 token 应失效（闭合「拿到前端 bundle → 伪造任意媒体 token」链）。
    未配独立密钥时回退 API_KEY（旧链接平滑迁移）。"""
    import hashlib
    import hmac
    import time

    from app.config import settings
    from app.services import media

    # 回退：未配独立密钥 → 用 API_KEY 签验一致
    monkeypatch.setattr(settings, "MEDIA_SIGNING_SECRET", None)
    monkeypatch.setattr(settings, "API_KEY", "bundle-key")
    assert media.verify(7, media.sign(7)) is True

    # 配独立密钥 → 签验走独立密钥；用 API_KEY 伪造的 token 失效
    monkeypatch.setattr(settings, "MEDIA_SIGNING_SECRET", "server-only-secret")
    assert media.verify(7, media.sign(7)) is True
    exp = int(time.time()) + 3600
    forged = hmac.new(b"bundle-key", f"7:{exp}".encode(), hashlib.sha256).hexdigest()[:16]
    assert media.verify(7, f"{exp}.{forged}") is False   # 攻击者用 bundle key 伪造 → 拒
