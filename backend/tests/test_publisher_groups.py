"""资本集团归属推导（报表口径）。中文夹具，用真实系谱形状造。

验收要点：
- structural 边（wholly_owned/controlling/affiliate）并组；**minority 不并组**
  （腾讯参股 10.17% 不得把元趣系吞进腾讯系——这条错了整张报表失真）
- 组名：根的 group_label > 组内任一成员的 label > 根主体名
- 根 = 组内无 structural 父者；多根/环取 id 最小，保证组名可重现
- 孤立主体不属任何组（返回值里没有它）
"""
import pytest

from app.services.publisher_groups import compute_groups


class _E:
    def __init__(self, id, name, group_label=None):
        self.id, self.name, self.group_label = id, name, group_label


class _R:
    def __init__(self, parent_id, child_id, relation_type="affiliate"):
        self.parent_id, self.child_id, self.relation_type = parent_id, child_id, relation_type


# 真实形状：腾讯 ⤳(minority) 元趣 →(affiliate) Funfly / Omnilojo / 江娱 →(affiliate) GAME SPARK / Game Lab
def _yuanqu_fixture():
    ents = [
        _E(38, "腾讯"), _E(35, "元趣娱乐", group_label="元趣系"), _E(4, "Funfly"),
        _E(17, "Omnilojo"), _E(33, "江娱互动"), _E(39, "GAME SPARK"), _E(121, "Game Lab Limited"),
        _E(99, "某独立小厂"),  # 无任何关系
    ]
    rels = [
        _R(38, 35, "minority"),      # 腾讯参股元趣 —— 不并组
        _R(35, 4), _R(35, 17), _R(35, 33),
        _R(33, 39), _R(33, 121),
    ]
    return ents, rels


def test_minority_does_not_merge_groups():
    """腾讯参股不并组：元趣系 6 家自成一组，腾讯不在组里。"""
    groups = compute_groups(*_yuanqu_fixture())
    assert 38 not in groups, "腾讯被 minority 边并进了元趣系"
    members = {i for i, g in groups.items() if g.group_id == 35}
    assert members == {35, 4, 17, 33, 39, 121}


def test_group_name_prefers_manual_label():
    """组名用根的手工标签「元趣系」，而非根主体名「元趣娱乐」。"""
    groups = compute_groups(*_yuanqu_fixture())
    assert groups[121].group_name == "元趣系"     # 两跳外的成员也继承组名
    assert groups[39].group_name == "元趣系"
    assert groups[121].group_id == 35            # 稳定键 = 根 id


def test_transitive_membership_two_hops():
    """Game Lab 经 江娱 两跳挂到元趣系——报表口径要的就是这个传递性。"""
    groups = compute_groups(*_yuanqu_fixture())
    assert groups[121].group_id == groups[35].group_id


def test_isolated_entity_has_no_group():
    """孤立主体不伪造「只有自己的集团」，与图谱『孤立主体不画』同口径。"""
    groups = compute_groups(*_yuanqu_fixture())
    assert 99 not in groups


def test_falls_back_to_root_name_without_label():
    ents = [_E(1, "FunPlus"), _E(2, "KingsGroup"), _E(3, "Puzala")]
    rels = [_R(1, 2, "wholly_owned"), _R(1, 3, "controlling")]
    groups = compute_groups(ents, rels)
    assert groups[2].group_name == "FunPlus" and groups[2].group_id == 1


def test_label_on_non_root_member_still_used():
    """标签打在组内任一成员上都生效（不强制必须打在根上）。"""
    ents = [_E(1, "母体"), _E(2, "子体", group_label="某某系")]
    groups = compute_groups(ents, [_R(1, 2, "controlling")])
    assert groups[1].group_name == "某某系"


def test_cycle_and_multi_root_are_deterministic():
    """环（互相 affiliate）无「无父节点」→ 取 id 最小当根，重跑结果一致。"""
    ents = [_E(7, "甲"), _E(5, "乙")]
    rels = [_R(7, 5, "affiliate"), _R(5, 7, "affiliate")]
    g1 = compute_groups(ents, rels)
    g2 = compute_groups(list(reversed(ents)), list(reversed(rels)))
    assert g1[7].group_id == g2[7].group_id == 5
    assert g1[5].group_name == g2[5].group_name == "乙"


def test_relation_to_deleted_entity_is_ignored():
    """指向已删主体的历史关系行不得让推导炸掉（KeyError）。"""
    ents = [_E(1, "在册主体")]
    groups = compute_groups(ents, [_R(1, 404, "controlling")])
    assert groups == {}


@pytest.mark.asyncio
async def test_list_endpoint_exposes_group(client):
    """端到端：建两主体 + affiliate 关系 + 组名 → 列表两条都带同一个 group_name。"""
    a = (await client.post("/api/publishers/", json={"name": "母集团", "is_slg": True})).json()
    b = (await client.post("/api/publishers/", json={"name": "旗下发行壳", "is_slg": True})).json()
    await client.post(f"/api/publishers/{a['id']}/relations", json={
        "counterpart_id": b["id"], "counterpart_role": "child", "relation_type": "affiliate",
    })
    await client.put(f"/api/publishers/{a['id']}", json={"group_label": "母集团系"})

    rows = (await client.get("/api/publishers/")).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[a["id"]]["group_name"] == "母集团系"
    assert by_id[b["id"]]["group_name"] == "母集团系"
    assert by_id[b["id"]]["group_id"] == a["id"]

    # 清空组名 → 回退根主体名
    await client.put(f"/api/publishers/{a['id']}", json={"group_label": ""})
    rows = (await client.get("/api/publishers/")).json()
    assert {r["id"]: r for r in rows}[b["id"]]["group_name"] == "母集团"
