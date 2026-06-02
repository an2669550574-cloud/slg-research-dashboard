"""AI 标签分析（P6）测试：scope 护栏 / 报告 + 追问 / 模型白名单 / 导出 md·csv。
中文数据（路型 / 投放时间）。LLM 调用全程 mock，不打公司网关。"""
import pytest

from app.services import tag_analysis
from app.schemas import MaterialTagValueItem


# ── 夹具 ──────────────────────────────────────────────────────────────────

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


def _live_ta():
    """取 client/app 夹具重导入后真正生效的 tag_analysis 模块（conftest 会清 sys.modules
    再重导入，模块顶层 `from app.services import tag_analysis` 拿到的是过期对象，patch 它
    不会影响运行中的 app）。"""
    import importlib
    return importlib.import_module("app.services.tag_analysis")


def _fake_llm(monkeypatch, store):
    """把 tag_analysis._call_llm 换成不打网关的假实现，并记录入参供断言。"""
    async def fake(model, data_block, history, user_text):
        store["model"] = model
        store["data_block"] = data_block
        store["history_len"] = len(history)
        store["user_text"] = user_text
        return f"# 分析结论\n基于 {len(data_block)} 字数据的回答。", 0.0123, {
            "input_tokens": 100, "output_tokens": 50,
        }
    monkeypatch.setattr(_live_ta(), "_call_llm", fake)


# ── 护栏 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejects_empty_scope(client, monkeypatch):
    _fake_llm(monkeypatch, {})
    r = await client.post("/api/tags/analysis", json={
        "mode": "report", "model": "claude-sonnet-4.5", "material_type": "playable",
    })
    assert r.status_code == 400
    assert "没有素材" in r.json()["detail"]


@pytest.mark.asyncio
async def test_rejects_over_limit(client, monkeypatch):
    _fake_llm(monkeypatch, {})
    monkeypatch.setattr(_live_ta(), "MATERIAL_LIMIT", 2)
    road, ropt = await _mk_text_dim(client, "路型", ["1路"])
    for t in ("甲", "乙", "丙"):
        await _mk_material(client, t, [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    r = await client.post("/api/tags/analysis", json={"mode": "report", "model": "claude-sonnet-4.5"})
    assert r.status_code == 400
    assert "上限" in r.json()["detail"] and "缩" in r.json()["detail"]


@pytest.mark.asyncio
async def test_rejects_bad_model(client, monkeypatch):
    _fake_llm(monkeypatch, {})
    road, ropt = await _mk_text_dim(client, "路型", ["1路"])
    await _mk_material(client, "甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    r = await client.post("/api/tags/analysis", json={"mode": "report", "model": "gpt-4o"})
    assert r.status_code == 400
    assert "不支持的模型" in r.json()["detail"]


@pytest.mark.asyncio
async def test_chat_requires_message(client, monkeypatch):
    _fake_llm(monkeypatch, {})
    road, ropt = await _mk_text_dim(client, "路型", ["1路"])
    await _mk_material(client, "甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    r = await client.post("/api/tags/analysis", json={"mode": "chat", "model": "claude-sonnet-4.5"})
    assert r.status_code == 400
    assert "追问内容不能为空" in r.json()["detail"]


# ── 报告 + 追问 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_creates_session_and_context(client, monkeypatch):
    store = {}
    _fake_llm(monkeypatch, store)
    road, ropt = await _mk_text_dim(client, "路型", ["1路", "2路"])
    await _mk_material(client, "甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    await _mk_material(client, "乙", [{"dimension_id": road["id"], "option_ids": [ropt["1路"], ropt["2路"]]}])

    r = await client.post("/api/tags/analysis", json={"mode": "report", "model": "claude-sonnet-4.5"})
    assert r.status_code == 200, r.text
    sess = r.json()
    # 会话 + 两条消息（user 指令 + assistant 回答）
    assert len(sess["messages"]) == 2
    assert sess["messages"][0]["role"] == "user"
    assert sess["messages"][1]["role"] == "assistant"
    assert sess["messages"][1]["material_count"] == 2
    assert sess["messages"][1]["cost_usd"] == 0.0123
    # 喂给 LLM 的 data_block 含分布聚合 + 逐素材标签明细（中文）
    db = store["data_block"]
    assert "标签分布聚合" in db and "路型" in db
    assert "1路×2" in db          # 甲、乙 都打了 1路
    assert "[素材 1] 甲" in db and "[素材 2] 乙" in db
    assert store["history_len"] == 0


@pytest.mark.asyncio
async def test_chat_follow_up_carries_history(client, monkeypatch):
    store = {}
    _fake_llm(monkeypatch, store)
    road, ropt = await _mk_text_dim(client, "路型", ["1路"])
    await _mk_material(client, "甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])

    first = (await client.post("/api/tags/analysis", json={
        "mode": "report", "model": "claude-sonnet-4.5",
    })).json()
    sid = first["id"]
    # 追问：带 session_id，沿用范围，历史应包含前两条
    r = await client.post("/api/tags/analysis", json={
        "session_id": sid, "mode": "chat", "model": "claude-sonnet-4.5",
        "message": "哪个路型素材最多？",
    })
    assert r.status_code == 200, r.text
    sess = r.json()
    assert len(sess["messages"]) == 4  # 报告(2) + 追问(2)
    assert sess["messages"][2]["content"] == "哪个路型素材最多？"
    assert store["history_len"] == 2   # 追问时把前两条历史发给了 LLM
    assert store["user_text"] == "哪个路型素材最多？"


# ── 列表 / 删除 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_and_delete(client, monkeypatch):
    _fake_llm(monkeypatch, {})
    road, ropt = await _mk_text_dim(client, "路型", ["1路"])
    await _mk_material(client, "甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    sid = (await client.post("/api/tags/analysis", json={
        "mode": "report", "model": "claude-sonnet-4.5",
    })).json()["id"]

    lst = (await client.get("/api/tags/analysis")).json()
    assert len(lst) == 1 and lst[0]["message_count"] == 2

    assert (await client.delete(f"/api/tags/analysis/{sid}")).status_code == 200
    assert (await client.get("/api/tags/analysis")).json() == []
    assert (await client.get(f"/api/tags/analysis/{sid}")).status_code == 404


# ── 导出 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_md_and_csv(client, monkeypatch):
    _fake_llm(monkeypatch, {})
    road, ropt = await _mk_text_dim(client, "路型", ["1路", "2路"])
    await _mk_material(client, "甲", [{"dimension_id": road["id"], "option_ids": [ropt["1路"]]}])
    await _mk_material(client, "乙", [{"dimension_id": road["id"], "option_ids": [ropt["2路"]]}])
    sid = (await client.post("/api/tags/analysis", json={
        "mode": "report", "model": "claude-sonnet-4.5",
    })).json()["id"]

    md = await client.get(f"/api/tags/analysis/{sid}/export.md")
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert f"tag-analysis-{sid}.md" in md.headers["content-disposition"]
    assert "分析结论" in md.text  # 会话转录含 assistant 回答；标签明细见 CSV 导出

    csv_resp = await client.get(f"/api/tags/analysis/{sid}/export.csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    body = csv_resp.text
    assert "一级标签,二级标签,素材数" in body
    assert "路型,1路,1" in body and "路型,2路,1" in body


# ── 纯函数单测：分布 / 分析块（不走 DB / 网关）──────────────────────────────

def test_distribution_dedup_per_material():
    # 一条素材同维度多值各计一次；跨素材累加
    item = lambda dim, val: MaterialTagValueItem(
        dimension_id=1, dimension_name=dim, value_type="text", option_id=1, value=val,
    )
    tag_map = {
        1: [item("路型", "1路"), item("路型", "2路")],   # 甲：1路+2路
        2: [item("路型", "1路")],                          # 乙：1路
    }
    dist = tag_analysis._distribution(tag_map)
    assert dict(dist["路型"]) == {"1路": 2, "2路": 1}


def test_analysis_block_with_and_without_analysis():
    from app.models.material import Material
    m = Material(id=1, title="甲", material_type="video")
    m.analysis_brief = "一个农场题材买量片"
    m.analysis_tags = ["农场", "合成"]
    m.analysis_scenes = [{"ts": 0, "description": "开场种地"}]
    m.analysis_hooks = [{"ts": 3, "kind": "卸负", "note": "快速收获"}]
    block = tag_analysis._analysis_block(m)
    assert "农场题材" in block and "合成" in block and "开场种地" in block and "卸负" in block

    empty = Material(id=2, title="乙", material_type="video")
    assert "尚未做 AI 分析" in tag_analysis._analysis_block(empty)
