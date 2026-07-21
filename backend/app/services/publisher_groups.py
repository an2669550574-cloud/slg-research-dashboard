"""资本集团归属推导（报表口径，2026-07）。

**为什么是推导而非手工字段**：117 个主体逐个填「所属集团」必然漂移，且会与资本树
各说各话。集团边界其实早被 `publisher_relations` 定义了，只是从没被聚合出来——本模块
把它算出来，报表口径与图谱口径永远同源。

**并组规则**（与前端 `equityGraph.ts GROUP_EDGE_TYPES` 必须一致，改一处要改两处）：
只有控制级 + 品牌型关联（wholly_owned / controlling / affiliate）并组；**纯参股
（minority）不并组**——否则腾讯参股 10.17% 会把元趣系整个吞进腾讯系，报表立刻失真。
这条边界是 `docs/PUBLISHERS.md`「L3 跨 entity 同款不合」当年判定「transitive merge
边界难定」的答案：难的是产品级合并，集团级并组有 structural/minority 这条现成的线。

**组名**：根主体的 `group_label` 优先（手工指定，如元趣娱乐 #35 → 「元趣系」），
否则回退根主体名。根 = 组内无 structural 父的主体；多个候选（环 / 多根）取 id 最小者，
保证同一份数据每次算出同一个组名（报表不能今天叫这个明天叫那个）。

**孤立主体**（无任何 structural 边）**不属于任何组**，返回 None——与图谱「孤立主体不画」
同口径。报表侧渲染成「独立主体」桶，不伪造一个只有自己的集团。
"""
from dataclasses import dataclass
from typing import Iterable, Optional

# 与前端 equityGraph.ts GROUP_EDGE_TYPES 同步：控制级 + 品牌型关联并组，参股不并组
GROUP_EDGE_TYPES = frozenset({"wholly_owned", "controlling", "affiliate"})


@dataclass(frozen=True)
class GroupInfo:
    """一个主体的集团归属。group_id = 组内根主体 id（稳定键，供前端分组/排序）。"""
    group_id: int
    group_name: str


def compute_groups(
    entities: Iterable,          # PublisherEntity 行（需 .id / .name / .group_label）
    relations: Iterable,         # PublisherRelation 行（需 .parent_id / .child_id / .relation_type）
) -> dict[int, GroupInfo]:
    """{entity_id: GroupInfo}。孤立主体不出现在返回值里（调用方按 None 处理）。

    纯函数、零 IO：调用方查好两张表传进来，测试可直接喂假对象。
    """
    ents = {e.id: e for e in entities}
    adj: dict[int, set[int]] = {}
    for r in relations:
        if r.relation_type not in GROUP_EDGE_TYPES:
            continue
        p, c = r.parent_id, r.child_id
        # 关系可能指向已删主体（历史行）——两端都在册才并组
        if p not in ents or c not in ents:
            continue
        adj.setdefault(p, set()).add(c)
        adj.setdefault(c, set()).add(p)

    # structural 父集合：判「谁是根」用（有向），与上面的无向邻接分开存
    parents: dict[int, set[int]] = {}
    for r in relations:
        if r.relation_type in GROUP_EDGE_TYPES and r.parent_id in ents and r.child_id in ents:
            parents.setdefault(r.child_id, set()).add(r.parent_id)

    out: dict[int, GroupInfo] = {}
    seen: set[int] = set()
    for start in sorted(adj):            # 排序遍历 = 结果与字典序无关，可重现
        if start in seen:
            continue
        comp: list[int] = []
        queue = [start]
        seen.add(start)
        while queue:
            cur = queue.pop(0)
            comp.append(cur)
            for nb in sorted(adj.get(cur, ())):
                if nb not in seen:
                    seen.add(nb)
                    queue.append(nb)
        comp_set = set(comp)
        # 根 = 组内没有 structural 父的主体；环/多根取 id 最小，保证组名稳定
        roots = [i for i in sorted(comp) if not (parents.get(i, set()) & comp_set)]
        root_id = roots[0] if roots else min(comp)
        root = ents[root_id]
        # 组名：根的手工标签优先；根没填则看组内任一有标签的主体（id 最小者），
        # 都没有才回退根主体名——这样「标签打在哪个成员上」不影响报表可用性。
        labeled = [i for i in sorted(comp) if (getattr(ents[i], "group_label", None) or "").strip()]
        if (getattr(root, "group_label", None) or "").strip():
            name = root.group_label.strip()
        elif labeled:
            name = ents[labeled[0]].group_label.strip()
        else:
            name = root.name
        info = GroupInfo(group_id=root_id, group_name=name)
        for i in comp:
            out[i] = info
    return out


async def load_groups(db) -> dict[int, GroupInfo]:
    """从库里读两张表算集团归属。端点/digest 共用，口径永不分叉。"""
    from sqlalchemy import select
    from app.models.publisher import PublisherEntity, PublisherRelation
    ents = (await db.execute(select(PublisherEntity))).scalars().all()
    rels = (await db.execute(select(PublisherRelation))).scalars().all()
    return compute_groups(ents, rels)


def group_of(groups: dict[int, GroupInfo], entity_id: Optional[int]) -> Optional[GroupInfo]:
    """None-safe 取组（entity_id 可能为 None：未归属产品行）。"""
    if entity_id is None:
        return None
    return groups.get(entity_id)
