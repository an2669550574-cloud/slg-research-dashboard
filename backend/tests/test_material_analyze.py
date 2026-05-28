"""素材 LLM 分析：端点护栏 + service 解析/兜底。

视觉调用本身需打太石网关 → 单测全部 mock 掉，确保不发真网络请求、
不依赖 ffmpeg/网关 key。ffmpeg 抽帧也 mock 成假数据。
"""
import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
FAKE_MP4 = b"\x00\x01fake-mp4"


def _media_tmp(monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))


async def _upload(client, kind="video", name="promo.mp4", mime="video/mp4"):
    payload = b"\x00\x01fake" if kind == "video" else PNG
    r = await client.post(
        "/api/materials/upload",
        data={"title": f"测试-{name}", "app_id": "g1", "material_type": kind,
              "platform": "tiktok", "tags": "ua"},
        files={"file": (name, payload, mime)},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ────────────────────────────────────────────────────────────
# 端点护栏
# ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_rejects_link_material(client):
    """外链素材没文件可抽帧，应直接拒绝。"""
    r = await client.post("/api/materials/", json={
        "app_id": "g1", "title": "External", "url": "https://example.com/v.mp4",
        "platform": "youtube", "material_type": "video", "tags": []
    })
    assert r.status_code == 201
    mid = r.json()["id"]
    r2 = await client.post(f"/api/materials/{mid}/analyze")
    assert r2.status_code == 400
    assert "外链" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_rejects_image_material(client, monkeypatch, tmp_path):
    """素材类型为 image 的应拒（仅视频可分析）。"""
    _media_tmp(monkeypatch, tmp_path)
    m = await _upload(client, kind="image", name="cover.png", mime="image/png")
    r = await client.post(f"/api/materials/{m['id']}/analyze")
    assert r.status_code == 400
    assert "视频" in r.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_404(client):
    r = await client.post("/api/materials/9999/analyze")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_analyze_budget_exhausted(client, monkeypatch, tmp_path):
    """当日成本超 LLM_DAILY_BUDGET_USD 应 429。"""
    _media_tmp(monkeypatch, tmp_path)
    m = await _upload(client)

    from app.services import video_analyze

    async def fake_today_cost(db):
        return 999.0  # 远超 budget

    monkeypatch.setattr(video_analyze, "today_cost_usd", fake_today_cost)
    r = await client.post(f"/api/materials/{m['id']}/analyze")
    assert r.status_code == 429
    assert "预算" in r.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_sets_running_and_returns_state(client, monkeypatch, tmp_path):
    """正常路径：端点立刻返回 running 状态；后台任务可 mock 成 noop。"""
    _media_tmp(monkeypatch, tmp_path)
    m = await _upload(client)

    from app.services import video_analyze

    async def fake_today_cost(db): return 0.0
    async def fake_analyze(mid): pass  # 不真跑

    monkeypatch.setattr(video_analyze, "today_cost_usd", fake_today_cost)
    monkeypatch.setattr(video_analyze, "analyze_material", fake_analyze)

    r = await client.post(f"/api/materials/{m['id']}/analyze")
    assert r.status_code == 200, r.text
    assert r.json()["analysis_status"] == "running"


@pytest.mark.asyncio
async def test_analyze_rejects_when_already_running(client, monkeypatch, tmp_path):
    """重入保护：同素材已 running 应 409。"""
    _media_tmp(monkeypatch, tmp_path)
    m = await _upload(client)

    # 直接更新 DB 把状态改成 running
    from app.database import AsyncSessionLocal
    from app.models.material import Material
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        obj = (await db.execute(select(Material).where(Material.id == m["id"]))).scalar_one()
        obj.analysis_status = "running"
        await db.commit()

    r = await client.post(f"/api/materials/{m['id']}/analyze")
    assert r.status_code == 409


# ────────────────────────────────────────────────────────────
# 采纳 LLM 标签
# ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adopt_tags_merges_dedup(client, monkeypatch, tmp_path):
    """analysis_tags + 已有 tags → 去重保序合并到 tags；analysis_tags 保留不动。"""
    _media_tmp(monkeypatch, tmp_path)
    m = await _upload(client)  # 默认 tags=["ua"]

    from app.database import AsyncSessionLocal
    from app.models.material import Material
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        obj = (await db.execute(select(Material).where(Material.id == m["id"]))).scalar_one()
        obj.analysis_tags = ["末日生存", "ua", "城建"]  # "ua" 已存在
        await db.commit()

    r = await client.post(f"/api/materials/{m['id']}/adopt-tags")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tags"] == ["ua", "末日生存", "城建"]  # 保留原顺序 + 新增追加
    assert body["analysis_tags"] == ["末日生存", "ua", "城建"]  # 原样保留


# ────────────────────────────────────────────────────────────
# Service: 响应解析容错
# ────────────────────────────────────────────────────────────

def test_parse_response_strips_code_fence():
    from app.services.video_analyze import _parse_response
    text = '```json\n{"brief": "测试", "tags": ["a"]}\n```'
    parsed = _parse_response(text)
    assert parsed["brief"] == "测试"
    assert parsed["tags"] == ["a"]


def test_parse_response_handles_prefix_garbage():
    """模型偶尔会带"以下是分析:"之类前缀；正则截取 { ... } 兜底。"""
    from app.services.video_analyze import _parse_response
    text = '以下是结果：{"brief": "X", "tags": []}'
    parsed = _parse_response(text)
    assert parsed["brief"] == "X"


def test_parse_response_raises_on_garbage():
    from app.services.video_analyze import _parse_response
    with pytest.raises(ValueError):
        _parse_response("not json at all")


# ────────────────────────────────────────────────────────────
# Service: 字段归一化
# ────────────────────────────────────────────────────────────

def test_norm_tags_trims_and_caps():
    from app.services.video_analyze import _norm_tags
    assert _norm_tags(["a", " b ", "", None, "c", "d", "e", "f", "g", "h", "i"]) == \
        ["a", "b", "c", "d", "e", "f", "g", "h"]


def test_norm_tags_invalid_returns_none():
    from app.services.video_analyze import _norm_tags
    assert _norm_tags(None) is None
    assert _norm_tags("not a list") is None


def test_norm_scenes_filters_invalid_ts():
    from app.services.video_analyze import _norm_scenes
    raw = [
        {"ts": 1.0, "description": "开场"},
        {"ts": "abc", "description": "坏 ts"},
        {"ts": 5.5, "description": ""},
        {"ts": 10, "description": "结尾"},
    ]
    assert _norm_scenes(raw) == [
        {"ts": 1.0, "description": "开场"},
        {"ts": 10.0, "description": "结尾"},
    ]


def test_norm_hooks_requires_kind_and_note():
    from app.services.video_analyze import _norm_hooks
    raw = [
        {"ts": 1.0, "kind": "CTA", "note": "下载"},
        {"ts": 2.0, "kind": "", "note": "missing kind"},
        {"ts": 3.0, "kind": "卸负", "note": ""},
    ]
    assert _norm_hooks(raw) == [{"ts": 1.0, "kind": "CTA", "note": "下载"}]


# ────────────────────────────────────────────────────────────
# llm_gateway: 成本估算
# ────────────────────────────────────────────────────────────

def test_estimate_cost_sonnet():
    from app.services.llm_gateway import estimate_cost
    # 10K input + 1K output @ sonnet-4.5 价目 (3 / 15)
    cost = estimate_cost("claude-sonnet-4.5", {"prompt_tokens": 10000, "completion_tokens": 1000})
    assert abs(cost.total_usd - (10000 * 3 / 1_000_000 + 1000 * 15 / 1_000_000)) < 1e-6


def test_estimate_cost_unknown_model_falls_back():
    """未知模型不应崩，按 sonnet 估即可。"""
    from app.services.llm_gateway import estimate_cost
    cost = estimate_cost("some-future-model", {"prompt_tokens": 1000, "completion_tokens": 100})
    assert cost.total_usd > 0


def test_estimate_cost_with_cache_read():
    """cache_read 命中的 token 从 input 里扣，按 cache_read 单价收。"""
    from app.services.llm_gateway import estimate_cost
    usage = {
        "prompt_tokens": 10000, "completion_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 8000},
    }
    cost = estimate_cost("claude-sonnet-4.5", usage)
    # billable_input = 2000, cache_read = 8000
    expected = 2000 * 3 / 1_000_000 + 8000 * 0.3 / 1_000_000
    assert abs(cost.total_usd - expected) < 1e-6


def test_get_client_raises_without_key(monkeypatch):
    from app.config import settings
    from app.services import llm_gateway
    monkeypatch.setattr(settings, "TAISHI_API_KEY", None)
    with pytest.raises(RuntimeError, match="TAISHI_API_KEY"):
        llm_gateway.get_client()
