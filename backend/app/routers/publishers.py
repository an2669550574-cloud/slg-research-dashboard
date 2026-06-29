"""厂商主体（publisher_entities）CRUD + 「主体→旗下产品」聚合。

主体 / 海外发行马甲 / 关注 app_id 三层在看板上维护，是 is_slg 判定的唯一数据源；
任何写操作后调 load_index_from_db() 刷新 slg_publishers 的内存索引，让榜单过滤 /
异动检测即时生效，无需重启。

「旗下产品」是查询态聚合——用主体的马甲 keyword 对 game_rankings.publisher 做 token
子序列匹配 + app_ids 精确匹配，跨已监测市场窗口合计下载/收入，零 ST 配额、纯本地库。
"""
import asyncio
import re
import time
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_, case, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, utcnow_naive
from app.models.publisher import (
    PublisherEntity, PublisherAlias, PublisherAppId, PublisherSource, PublisherRelation,
    PublisherItunesArtist, PublisherItunesApp, PublisherIgnore,
)
from app.models.game import GameRanking, CHART_GROSSING, CHART_FREE
from app.models.newcomer import MarketNewcomerLog
from app.schemas import (
    PublisherEntityOut, PublisherEntityCreate, PublisherEntityUpdate,
    PublisherAliasOut, PublisherAliasCreate,
    PublisherAppIdOut, PublisherAppIdCreate,
    PublisherSourceOut, PublisherSourceCreate,
    PublisherItunesArtistOut, PublisherItunesArtistCreate,
    PublisherRelationCreate, PublisherRelationLinkOut, PublisherProductOut,
    PublisherTopProductOut, PublisherGapOut, PublisherHealthOut,
    PublisherIgnoreOut, PublisherIgnoreCreate, PublisherArtistSuggestionOut,
    PublisherDownloadLeadOut,
)
from app.services.slg_publishers import load_index_from_db
from app.services.provenance import is_primary, provenance_tier
from app.services.name_match import corp_squash

router = APIRouter(prefix="/api/publishers", tags=["publishers"])

_NORM = re.compile(r"[^a-z0-9]+")


def _toks(s: str | None) -> list[str]:
    return _NORM.sub(" ", (s or "").lower()).split()


def _kw_hit(pub_tokens: list[str], kw_tokens: tuple[str, ...]) -> bool:
    """kw_tokens 作为连续子序列出现在 pub_tokens 里即命中（与 is_slg_publisher 同规则）。"""
    n = len(kw_tokens)
    if n == 0:
        return False
    for i in range(len(pub_tokens) - n + 1):
        if tuple(pub_tokens[i:i + n]) == kw_tokens:
            return True
    return False


def _alias_squash_set(aliases) -> set[str]:
    """主体马甲集合的 squash 键集（去法人后缀拼接，连写回退用）。空键剔除。"""
    return {s for s in (corp_squash(_toks(a.keyword)) for a in aliases) if s}


def _pub_hit(pub_tokens: list[str], alias_kw_tokens, alias_squashes: set[str]) -> bool:
    """publisher 命中某主体：alias token 子序列命中 **或** 去后缀 squash 整段等值。
    后者修 "Topgames.Inc" 配不上 alias "top games" 的连写错位（见 name_match）。"""
    if any(_kw_hit(pub_tokens, kt) for kt in alias_kw_tokens):
        return True
    return bool(alias_squashes) and corp_squash(pub_tokens) in alias_squashes


async def _get_entity_or_404(entity_id: int, db: AsyncSession) -> PublisherEntity:
    e = (await db.execute(
        select(PublisherEntity).where(PublisherEntity.id == entity_id)
    )).scalar_one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="厂商主体不存在")
    return e


async def _children(entity_id: int, db: AsyncSession):
    aliases = (await db.execute(
        select(PublisherAlias).where(PublisherAlias.entity_id == entity_id)
        .order_by(PublisherAlias.id)
    )).scalars().all()
    app_ids = (await db.execute(
        select(PublisherAppId).where(PublisherAppId.entity_id == entity_id)
        .order_by(PublisherAppId.id)
    )).scalars().all()
    return aliases, app_ids


async def _itunes_artists(entity_id: int, db: AsyncSession):
    return (await db.execute(
        select(PublisherItunesArtist).where(PublisherItunesArtist.entity_id == entity_id)
        .order_by(PublisherItunesArtist.id)
    )).scalars().all()


async def _sources(entity_id: int, db: AsyncSession):
    return (await db.execute(
        select(PublisherSource).where(PublisherSource.entity_id == entity_id)
        .order_by(PublisherSource.id)
    )).scalars().all()


def _source_out(s: PublisherSource) -> PublisherSourceOut:
    return PublisherSourceOut(
        id=s.id, url=s.url, title=s.title, source_type=s.source_type,
        is_primary=is_primary(s.source_type), confidence=s.confidence,
        as_of=s.as_of, note=s.note,
    )


def _rel_link(rel: PublisherRelation, counterpart_id: int, name_map: dict[int, str]) -> PublisherRelationLinkOut:
    return PublisherRelationLinkOut(
        relation_id=rel.id, entity_id=counterpart_id, name=name_map.get(counterpart_id, "?"),
        relation_type=rel.relation_type, stake_pct=rel.stake_pct, note=rel.note,
    )


async def _relations(entity_id: int, db: AsyncSession):
    """返回 (parents, children)：本主体的母公司/投资方、子公司/关联（对方名已解析）。"""
    rels = (await db.execute(
        select(PublisherRelation).where(
            or_(PublisherRelation.parent_id == entity_id, PublisherRelation.child_id == entity_id)
        ).order_by(PublisherRelation.id)
    )).scalars().all()
    cp_ids = {(r.child_id if r.parent_id == entity_id else r.parent_id) for r in rels}
    name_map: dict[int, str] = {}
    if cp_ids:
        rows = (await db.execute(
            select(PublisherEntity.id, PublisherEntity.name).where(PublisherEntity.id.in_(cp_ids))
        )).all()
        name_map = {i: n for i, n in rows}
    parents, children = [], []
    for r in rels:
        if r.child_id == entity_id:  # 对方是母公司
            parents.append(_rel_link(r, r.parent_id, name_map))
        if r.parent_id == entity_id:  # 对方是子公司
            children.append(_rel_link(r, r.child_id, name_map))
    return parents, children


async def _ranking_pairs(db: AsyncSession):
    """全部曾上榜 app 的 (app_id, 代表 publisher, 名字, icon, 收入分) —— 一次 GROUP BY
    供批量算各主体旗下产品数 + 取折叠态图标锚点（按收入降序的 top3）。零 ST 配额。

    name/publisher/icon 都用 **US 优先 + fallback MAX**：同 iOS app_id 在 US/JP/KR 多市场
    返回本地化名（"Whiteout Survival"/"ホワイトアウト・サバイバル"/"화이트아웃 서바이벌"），
    MAX 按 Unicode 排序会偏向 CJK 字符吃掉 Latin 原名；用 US 行优先解 → 拿到 Latin 名，
    跨平台 sibling 去重（_dedup_siblings）才能匹得上同款。无 US 上榜 → fallback MAX。
    """
    us_name = case((GameRanking.country == "US", GameRanking.name))
    us_pub  = case((GameRanking.country == "US", GameRanking.publisher))
    us_icon = case((GameRanking.country == "US", GameRanking.icon_url))
    res = await db.execute(
        select(
            GameRanking.app_id,
            func.coalesce(func.max(us_pub), func.max(GameRanking.publisher)),
            func.coalesce(func.max(us_name), func.max(GameRanking.name)),
            func.coalesce(func.max(us_icon), func.max(GameRanking.icon_url)),
            func.max(GameRanking.revenue),
        ).where(GameRanking.chart_type == CHART_GROSSING).group_by(GameRanking.app_id)
    )
    return [(app_id, pub, name, icon, rev) for app_id, pub, name, icon, rev in res.all()]


# 进程内 TTL cache：榜单同步是定时任务（非请求触发），60s 陈旧不影响判断。
# 把 game_rankings 全表 GROUP BY 从「每次 list 跑一遍」降到「每分钟跑一遍」。
# alias/app_id 的写操作不需要 invalidate——它们不改 game_rankings 本身，匹配在 cache 外算。
_PAIRS_CACHE_TTL = 60.0
_pairs_cache: tuple[float, list] | None = None
_rank_cache: tuple[float, dict[str, tuple[int, str]]] | None = None


async def _ranking_pairs_cached(db: AsyncSession):
    global _pairs_cache
    now = time.monotonic()
    if _pairs_cache and now - _pairs_cache[0] < _PAIRS_CACHE_TTL:
        return _pairs_cache[1]
    pairs = await _ranking_pairs(db)
    _pairs_cache = (now, pairs)
    return pairs


def _prefer_market(rank: int, market: str, cur: tuple[int, str] | None) -> bool:
    """是否用 (rank, market) 替换 cur：名次更小者更优；名次相同则优先美国（US/... 市场），
    其余（非美 vs 非美）保持先到先得。统一卡片/列表/详情对「同名次取哪国榜」的口径。"""
    if cur is None:
        return True
    if rank != cur[0]:
        return rank < cur[0]
    return market.startswith("US/") and not cur[1].startswith("US/")


async def _rank_by_app(db: AsyncSession) -> dict[str, tuple[int, str]]:
    """{app_id: (跨市场最佳名次, 命中市场如 "JP/android")}。只看各 (国家,平台) **最新一期**
    快照——反映「当前畅销」而非历史最好，供「按畅销榜名次」排序。零 ST 配额、纯本地库。"""
    latest = (
        select(GameRanking.country, GameRanking.platform,
               func.max(GameRanking.date).label("md"))
        .where(GameRanking.chart_type == CHART_GROSSING)
        .group_by(GameRanking.country, GameRanking.platform)
    ).subquery()
    rows = (await db.execute(
        select(GameRanking.app_id, GameRanking.country, GameRanking.platform, GameRanking.rank)
        .join(latest, and_(GameRanking.country == latest.c.country,
                           GameRanking.platform == latest.c.platform,
                           GameRanking.date == latest.c.md))
        .where(GameRanking.chart_type == CHART_GROSSING)
    )).all()
    best: dict[str, tuple[int, str]] = {}
    for app_id, country, platform, rank in rows:
        if rank is None:
            continue
        market = f"{country}/{platform}"
        if _prefer_market(rank, market, best.get(app_id)):
            best[app_id] = (rank, market)
    return best


async def _rank_by_app_cached(db: AsyncSession) -> dict[str, tuple[int, str]]:
    global _rank_cache
    now = time.monotonic()
    if _rank_cache and now - _rank_cache[0] < _PAIRS_CACHE_TTL:
        return _rank_cache[1]
    rb = await _rank_by_app(db)
    _rank_cache = (now, rb)
    return rb


def _match_for_entity(pairs, alias_kw_tokens, app_id_set, itunes_products=(), rank_by_app=None,
                      alias_squashes=frozenset()):
    """返回 (旗下产品数, 按收入降序的 top3 PublisherTopProductOut, 最佳名次, 命中市场)。
    rank_by_app 给定时算旗下产品在各市场最新快照的最小名次（best_rank/market），否则两者为 None。

    两个来源并集：
    - **榜单 game_rankings**（pairs）：app_id 精确钉 或 alias token 命中代表 publisher；带收入。
    - **雷达 itunes_apps**（itunes_products，按 entity_id 直挂）：开发者账号下的 app 就是
      旗下产品，含**未上榜的软启动新品**（如新厂商主打新品）——这类产品永远进不了
      榜单聚合，只能从雷达补。无收入、publisher 未知，故不参与跨平台 sibling 合并。

    跨平台去重：matched 按 app_id 聚集后，过 _dedup_siblings 按 entity-scope 名字 prefix
    合并 iOS+Android 同款（无需 publisher 字符串等价——同 entity 内的 alias 已锚定）。
    product_count = 去重后组数，top3 = 组级收入 top3。best_rank 仍按所有 member app_id 取最小
    （同款多平台谁名次高用谁）。"""
    matched: dict[str, tuple[float, str, str, str | None]] = {}  # app_id -> (revenue, name, icon, publisher)
    for app_id, pub, name, icon, rev in pairs:
        hit = app_id in app_id_set or _pub_hit(_toks(pub), alias_kw_tokens, alias_squashes)
        if hit:
            matched[app_id] = (rev or 0, name, icon, pub)
    seen_names = {(n or "").strip().lower() for _, n, _, _ in matched.values() if n}
    for track_id, name, artwork, _genre in itunes_products:
        if track_id in matched:
            continue
        key = (name or "").strip().lower()
        if key and key in seen_names:
            continue  # 同名跨平台去重（雷达里 iOS+GP 各一条同款）
        if key:
            seen_names.add(key)
        matched[track_id] = (0, name, artwork, None)  # 雷达 app 也参与 entity-scope sibling 合并（按名字）
    groups = _dedup_siblings(matched)
    top = [PublisherTopProductOut(app_id=g["app_id"], name=g["name"], icon_url=g["icon_url"]) for g in groups[:3]]
    best_rank: int | None = None
    best_market: str | None = None
    if rank_by_app:
        for g in groups:
            for aid in g["member_app_ids"]:
                hit = rank_by_app.get(aid)
                cur = (best_rank, best_market) if best_rank is not None else None
                if hit and _prefer_market(hit[0], hit[1], cur):
                    best_rank, best_market = hit
    return len(groups), top, best_rank, best_market


_NORM_FOR_SIBLING = re.compile(r"[^a-z0-9]")


def _norm_for_sibling(s: str | None) -> str:
    """与 sibling_match.normalize_ident 同口径：去大小写 + 删非字母数字（含 CJK / 标点 / 空格）。"""
    return _NORM_FOR_SIBLING.sub("", (s or "").lower())


def _dedup_siblings(matched: dict[str, tuple]) -> list[dict]:
    """**entity-scoped** 跨平台同款游戏去重（名字 prefix 子序列匹配 ≥5）。

    输入 matched: {app_id: (revenue, name, icon, publisher_or_None)}
    输出每组一行：{app_id (代表=收入最高的 member), name (本组里最长 Latin 名，否则原名),
                  icon_url, revenue (本组合计), downloads (始终 0，调用方按需补), member_app_ids}
    按 revenue 降序排好。

    **前提：调用方保证 matched 内所有 app_id 都属于同一个 entity**（通过 alias/app_id
    钉死命中）。本函数仅按名字 prefix 合并，不再校验 publisher 字符串等价。理由：
    同一家公司不同法人/分公司/简写常用不同 publisher 字符串（"TG Inc." vs "TOP GAMES INC."、
    "IGG SINGAPORE PTE. LTD." vs "IGG.COM"、"InnoGames GmbH" vs "InnoGames"、
    "Leyi Classic Games" vs "LEXIANGCO.,LIMITED"），而它们的 alias 已全部归到同一 entity——
    再加 publisher 字符串等价检查会把这些合法的跨平台同款拒之门外。

    名字 prefix ≥5 字符是核心安全网：能合并 "Whiteout Survival"/"Whiteout Survival"
    自匹配、"Evony"/"Evony: The King's Return" 这类带后缀差异的同款；同时阻止
    "Last War" vs "Last Z" 这类只共享 4 字符前缀的不同游戏被误合。

    CJK-only 本地化名（norm 后为空字符串）不参与匹配 → 保留为独立组，避免
    "游戏一"/"游戏二" 这类无锚名因 norm 后都为空被误合。

    与 services/sibling_match.py（详情页全表扫描、无 entity scope）有意分流：
    那里保留 publisher 字符串等价检查作为安全网，本函数不需要。
    """
    if not matched:
        return []
    items = list(matched.items())
    n = len(items)
    # 只保留名字归一键参与合并：publisher 字符串等价校验已于 #91 移除（见 docstring），
    # 原本为它准备的 pub / _norm_for_sibling(pub) 已无任何读取点，删掉避免误导。
    enriched = [(aid, rev, name, icon, _norm_for_sibling(name))
                for aid, (rev, name, icon, _pub) in items]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        nni = enriched[i][4]
        if not nni:
            continue  # 纯 CJK 名 norm 后空 → 不参与合并
        for j in range(i + 1, n):
            nnj = enriched[j][4]
            if not nnj:
                continue
            short, long = (nni, nnj) if len(nni) <= len(nnj) else (nnj, nni)
            if len(short) >= 5 and long.startswith(short):
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out: list[dict] = []
    for member_indexes in groups.values():
        members = [enriched[i] for i in member_indexes]
        members.sort(key=lambda m: -m[1])  # by revenue desc
        rep_aid, rep_rev, rep_name, rep_icon, _ = members[0]
        # 偏好最长的「含 Latin 字母」名字（同款 iOS+Android 用 US 优先后大概率拿到 Latin）
        latin_names = [m[2] for m in members if m[2] and any('a' <= c.lower() <= 'z' for c in m[2])]
        if latin_names:
            rep_name = max(latin_names, key=len)
        out.append({
            "app_id": rep_aid,
            "name": rep_name,
            "icon_url": rep_icon,
            "revenue": sum(m[1] for m in members),
            "member_app_ids": [m[0] for m in members],
        })
    out.sort(key=lambda g: -g["revenue"])
    return out


def _compute_all_matches(
    pairs,
    by_alias: dict[int, list],
    by_appid: dict[int, list],
    itunes_by_entity: dict[int, list[tuple]],
    rank_by_app: dict[str, tuple[int, str]] | None,
    entity_ids,
):
    """list 端点专用：一次过算所有 entity 的 (count, top3, best_rank, best_market)。

    倒排索引：alias 的第一个 token → [(kw_tokens, entity_id), ...]。扫一遍 pairs，
    每行 publisher 算一次 tokens，按位置 i 查 first-token 候选再校验剩余 tokens。
    复杂度从 O(entities × pairs × aliases) 降到 O(pairs × token_candidates)。
    单 entity 端点（get/create/update 返回值）继续走 _match_for_entity（N=1 不需要倒排）。
    """
    # 倒排：first_token → [(kw_tokens, entity_id), ...]
    alias_idx: dict[str, list[tuple[tuple[str, ...], int]]] = {}
    # squash 倒排：alias 去后缀拼接键 → [entity_id, ...]（连写/法人后缀回退，同 _pub_hit）
    squash_idx: dict[str, list[int]] = {}
    for eid, aliases in by_alias.items():
        for a in aliases:
            toks = _toks(a.keyword)
            t = tuple(toks)
            if t:
                alias_idx.setdefault(t[0], []).append((t, eid))
            sq = corp_squash(toks)
            if sq:
                squash_idx.setdefault(sq, []).append(eid)
    # app_id → [entity_id, ...]（一个 app_id 理论上可挂多个主体，保险起见用 list）
    app_id_owners: dict[str, list[int]] = {}
    for eid, app_ids in by_appid.items():
        for a in app_ids:
            app_id_owners.setdefault(a.app_id, []).append(eid)

    # entity_id → {app_id: (revenue, name, icon, publisher)}
    matched: dict[int, dict[str, tuple[float, str, str, str | None]]] = {eid: {} for eid in entity_ids}

    for app_id, pub, name, icon, rev in pairs:
        for eid in app_id_owners.get(app_id, ()):
            matched[eid][app_id] = (rev or 0, name, icon, pub)
        if not pub:
            continue
        pub_tokens = _toks(pub)
        if not pub_tokens:
            continue
        hit_eids: set[int] = set()  # 同一 (app_id,pub) 被同主体多 alias 命中只记一次
        for i, tok in enumerate(pub_tokens):
            for kw_tokens, eid in alias_idx.get(tok, ()):
                if eid in hit_eids:
                    continue
                n = len(kw_tokens)
                if i + n > len(pub_tokens):
                    continue
                if tuple(pub_tokens[i:i + n]) == kw_tokens:
                    matched[eid][app_id] = (rev or 0, name, icon, pub)
                    hit_eids.add(eid)
        # 连写/法人后缀回退：squash 整段等值（同 _pub_hit 第二路径）
        for eid in squash_idx.get(corp_squash(pub_tokens), ()):
            if eid not in hit_eids:
                matched[eid][app_id] = (rev or 0, name, icon, pub)
                hit_eids.add(eid)

    out: dict[int, tuple[int, list, int | None, str | None]] = {}
    for eid in entity_ids:
        m = matched[eid]
        seen_names = {(n or "").strip().lower() for _, n, _, _ in m.values() if n}
        for track_id, iname, artwork, _genre in itunes_by_entity.get(eid, ()):
            if track_id in m:
                continue
            key = (iname or "").strip().lower()
            if key and key in seen_names:
                continue
            if key:
                seen_names.add(key)
            m[track_id] = (0, iname, artwork, None)  # 雷达 app 也参与 entity-scope sibling 合并（按名字）
        groups = _dedup_siblings(m)
        top = [PublisherTopProductOut(app_id=g["app_id"], name=g["name"], icon_url=g["icon_url"]) for g in groups[:3]]
        best_rank: int | None = None
        best_market: str | None = None
        if rank_by_app:
            for g in groups:
                for aid in g["member_app_ids"]:
                    hit = rank_by_app.get(aid)
                    cur = (best_rank, best_market) if best_rank is not None else None
                    if hit and _prefer_market(hit[0], hit[1], cur):
                        best_rank, best_market = hit
        out[eid] = (len(groups), top, best_rank, best_market)
    return out


async def _itunes_products_by_entity(db: AsyncSession) -> dict[int, list[tuple]]:
    """一次查全表：{entity_id: [(track_id, name, artwork_url, genre), ...]}（list 端点批量用）。"""
    rows = (await db.execute(
        select(PublisherItunesApp.entity_id, PublisherItunesApp.track_id,
               PublisherItunesApp.name, PublisherItunesApp.artwork_url, PublisherItunesApp.genre)
        .order_by(PublisherItunesApp.id)
    )).all()
    out: dict[int, list[tuple]] = {}
    for entity_id, track_id, name, artwork, genre in rows:
        out.setdefault(entity_id, []).append((track_id, name, artwork, genre))
    return out


async def _itunes_products(entity_id: int, db: AsyncSession) -> list[tuple]:
    """单主体雷达 app 清单 (track_id, name, artwork_url, genre)（get/create/update + products 用）。"""
    rows = (await db.execute(
        select(PublisherItunesApp.track_id, PublisherItunesApp.name,
               PublisherItunesApp.artwork_url, PublisherItunesApp.genre)
        .where(PublisherItunesApp.entity_id == entity_id)
        .order_by(PublisherItunesApp.id)
    )).all()
    return [(track_id, name, artwork, genre) for track_id, name, artwork, genre in rows]


def _build_out(e: PublisherEntity, aliases, app_ids, sources, parents, children,
               product_count: int | None, itunes_artists=(), top_products=(),
               best_rank: int | None = None, best_market: str | None = None) -> PublisherEntityOut:
    return PublisherEntityOut(
        id=e.id, name=e.name, name_en=e.name_en, hq_region=e.hq_region,
        is_slg=e.is_slg, brief=e.brief, sort_order=e.sort_order,
        aliases=[PublisherAliasOut.model_validate(a) for a in aliases],
        app_ids=[PublisherAppIdOut.model_validate(a) for a in app_ids],
        itunes_artists=[PublisherItunesArtistOut.model_validate(a) for a in itunes_artists],
        sources=[_source_out(s) for s in sources],
        provenance_tier=provenance_tier([s.source_type for s in sources]),
        parents=parents, children=children,
        product_count=product_count, top_products=list(top_products),
        best_rank=best_rank, best_rank_market=best_market,
        created_at=e.created_at, updated_at=e.updated_at,
    )


@router.get("/", response_model=list[PublisherEntityOut])
async def list_publishers(db: AsyncSession = Depends(get_db)):
    """全部主体（含马甲 / app_id / 旗下产品数），按 sort_order、name 排。"""
    entities = (await db.execute(
        select(PublisherEntity).order_by(PublisherEntity.sort_order, PublisherEntity.name)
    )).scalars().all()
    all_aliases = (await db.execute(select(PublisherAlias).order_by(PublisherAlias.id))).scalars().all()
    all_app_ids = (await db.execute(select(PublisherAppId).order_by(PublisherAppId.id))).scalars().all()
    all_artists = (await db.execute(select(PublisherItunesArtist).order_by(PublisherItunesArtist.id))).scalars().all()
    all_sources = (await db.execute(select(PublisherSource).order_by(PublisherSource.id))).scalars().all()
    all_relations = (await db.execute(select(PublisherRelation).order_by(PublisherRelation.id))).scalars().all()
    pairs = await _ranking_pairs_cached(db)
    rank_by_app = await _rank_by_app_cached(db)
    itunes_by_entity = await _itunes_products_by_entity(db)

    by_alias: dict[int, list[PublisherAlias]] = {}
    for a in all_aliases:
        by_alias.setdefault(a.entity_id, []).append(a)
    by_appid: dict[int, list[PublisherAppId]] = {}
    for a in all_app_ids:
        by_appid.setdefault(a.entity_id, []).append(a)
    by_artist: dict[int, list[PublisherItunesArtist]] = {}
    for a in all_artists:
        by_artist.setdefault(a.entity_id, []).append(a)
    by_source: dict[int, list[PublisherSource]] = {}
    for s in all_sources:
        by_source.setdefault(s.entity_id, []).append(s)

    name_map = {e.id: e.name for e in entities}
    by_parents: dict[int, list[PublisherRelationLinkOut]] = {}   # entity 作为 child → 它的母公司
    by_children: dict[int, list[PublisherRelationLinkOut]] = {}  # entity 作为 parent → 它的子公司
    for r in all_relations:
        by_parents.setdefault(r.child_id, []).append(_rel_link(r, r.parent_id, name_map))
        by_children.setdefault(r.parent_id, []).append(_rel_link(r, r.child_id, name_map))

    entity_ids = [e.id for e in entities]
    match_by_entity = _compute_all_matches(
        pairs, by_alias, by_appid, itunes_by_entity, rank_by_app, entity_ids)

    out = []
    for e in entities:
        count, top, best_rank, best_market = match_by_entity[e.id]
        out.append(_build_out(
            e, by_alias.get(e.id, []), by_appid.get(e.id, []), by_source.get(e.id, []),
            by_parents.get(e.id, []), by_children.get(e.id, []),
            count, itunes_artists=by_artist.get(e.id, []), top_products=top,
            best_rank=best_rank, best_market=best_market,
        ))
    return out


@router.post("/", response_model=PublisherEntityOut, status_code=201)
async def create_publisher(data: PublisherEntityCreate, db: AsyncSession = Depends(get_db)):
    e = PublisherEntity(
        name=data.name, name_en=data.name_en, hq_region=data.hq_region,
        is_slg=data.is_slg, brief=data.brief, sort_order=data.sort_order,
    )
    db.add(e)
    await db.flush()
    # payload 内去重，与 add_alias / add_app_id 端点的幂等口径对齐：新主体下不写重复马甲行。
    # 内联只需防本次 payload 自带重复（实体刚建、DB 里还没有它的子行），故 strip 后按 seen 集去重。
    seen_kw: set[str] = set()
    for a in data.aliases:
        kw = a.keyword.strip()
        if kw in seen_kw:
            continue
        seen_kw.add(kw)
        db.add(PublisherAlias(entity_id=e.id, keyword=kw, label=a.label))
    seen_aid: set[str] = set()
    for ap in data.app_ids:
        aid = ap.app_id.strip()
        if aid in seen_aid:
            continue
        seen_aid.add(aid)
        db.add(PublisherAppId(entity_id=e.id, app_id=aid, note=ap.note))
    await db.commit()
    await db.refresh(e)
    await load_index_from_db()
    aliases, app_ids = await _children(e.id, db)
    sources = await _sources(e.id, db)
    parents, children = await _relations(e.id, db)
    pairs = await _ranking_pairs_cached(db)
    rank_by_app = await _rank_by_app_cached(db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    count, top, best_rank, best_market = _match_for_entity(
        pairs, kw_tokens, {a.app_id for a in app_ids},
        await _itunes_products(e.id, db), rank_by_app, _alias_squash_set(aliases))
    return _build_out(e, aliases, app_ids, sources, parents, children, count,
                      itunes_artists=await _itunes_artists(e.id, db), top_products=top,
                      best_rank=best_rank, best_market=best_market)


@router.get("/health", response_model=PublisherHealthOut)
async def publisher_health(db: AsyncSession = Depends(get_db)):
    """主体模块数据健康度自检——把多轮 audit sweep 用的手写脚本固化成端点。

    维度：溯源 tier 分布 / 待补 backlog / 命名 backlog / 复核 backlog / 总量统计。
    零 ST 配额、纯本地 DB；驱动 PublishersManage 顶部健康度小卡 + curl 周报场景。
    必须先于 GET /{entity_id} 声明，否则 'health' 会被 int 路径捕获 → 422。
    """
    from datetime import datetime
    entities = (await db.execute(select(PublisherEntity))).scalars().all()
    aliases = (await db.execute(select(PublisherAlias))).scalars().all()
    app_ids = (await db.execute(select(PublisherAppId))).scalars().all()
    sources = (await db.execute(select(PublisherSource))).scalars().all()
    relations = (await db.execute(select(PublisherRelation))).scalars().all()
    # iTunes-artist 雷达：唯一不依赖产品进榜的自动召回器，但只对手动 wire 过 artist_id 的
    # iOS 账号生效。追踪覆盖率，让「王牌空转」可见、可被周报驱动补全（见 PUBLISHERS.md 审查）。
    ios_artists = (await db.execute(
        select(PublisherItunesArtist).where(PublisherItunesArtist.platform == "ios")
    )).scalars().all()
    entities_with_ios_artist = {a.entity_id for a in ios_artists}

    aliases_by_eid: dict[int, int] = {}
    for a in aliases:
        aliases_by_eid[a.entity_id] = aliases_by_eid.get(a.entity_id, 0) + 1
    appids_by_eid: dict[int, int] = {}
    for a in app_ids:
        appids_by_eid[a.entity_id] = appids_by_eid.get(a.entity_id, 0) + 1
    sources_by_eid: dict[int, list] = {}
    for s in sources:
        sources_by_eid.setdefault(s.entity_id, []).append(s)
    rels_by_eid: dict[int, int] = {}
    for r in relations:
        rels_by_eid[r.parent_id] = rels_by_eid.get(r.parent_id, 0) + 1
        rels_by_eid[r.child_id] = rels_by_eid.get(r.child_id, 0) + 1

    tier_primary = tier_secondary = tier_none = 0
    empty_brief = no_sources = no_primary_source = no_relations = 0
    no_aliases_no_appids = cn_no_chinese_name = stale_review = 0
    capital_entities = 0
    brief_lens: list[int] = []
    now = datetime.utcnow()

    for e in entities:
        srcs = sources_by_eid.get(e.id, [])
        n_pri = sum(1 for s in srcs if is_primary(s.source_type))
        tier = provenance_tier([s.source_type for s in srcs])
        if tier == "primary":
            tier_primary += 1
        elif tier == "secondary":
            tier_secondary += 1
        else:
            tier_none += 1

        blen = len((e.brief or "").strip())
        brief_lens.append(blen)
        if blen == 0:
            empty_brief += 1
        if not srcs:
            no_sources += 1
        elif n_pri == 0:
            no_primary_source += 1
        if rels_by_eid.get(e.id, 0) == 0:
            no_relations += 1
        if aliases_by_eid.get(e.id, 0) == 0 and appids_by_eid.get(e.id, 0) == 0:
            no_aliases_no_appids += 1
        # 国内厂未中文化：hq=国内 但 name 全无 CJK
        if e.hq_region == "国内" and not any("一" <= c <= "鿿" for c in (e.name or "")):
            cn_no_chinese_name += 1
        if not e.is_slg:
            capital_entities += 1
        # 复核 backlog：有源、最新 as_of ≥ 12 个月（与前端 isStaleForReview 同口径）
        as_ofs = [s.as_of for s in srcs if s.as_of]
        if as_ofs:
            latest = max(as_ofs)  # ISO 字符串排序对 YYYY-MM-DD 前缀有序
            try:
                # 容忍 "YYYY" / "YYYY-MM" / "YYYY-MM-DD"
                parts = latest.split("-")
                y, m, d = int(parts[0]), int(parts[1]) if len(parts) > 1 else 1, int(parts[2]) if len(parts) > 2 else 1
                months = (now.year - y) * 12 + (now.month - m) - (1 if now.day < d else 0)
                if months >= 12:
                    stale_review += 1
            except (ValueError, IndexError):
                pass

    total = len(entities)
    return PublisherHealthOut(
        total=total,
        tier_primary=tier_primary, tier_secondary=tier_secondary, tier_none=tier_none,
        empty_brief=empty_brief, no_sources=no_sources, no_primary_source=no_primary_source,
        no_relations=no_relations, no_aliases_no_appids=no_aliases_no_appids,
        cn_no_chinese_name=cn_no_chinese_name, stale_review=stale_review,
        total_aliases=len(aliases), total_app_ids=len(app_ids),
        total_sources=len(sources), total_relations=len(relations),
        total_itunes_artists=len(ios_artists),
        entities_without_itunes_artist=sum(
            1 for e in entities if e.id not in entities_with_ios_artist),
        capital_entities=capital_entities,
        avg_brief_len=(sum(brief_lens) // total) if total else 0,
        max_brief_len=max(brief_lens) if brief_lens else 0,
    )


@router.get("/gaps", response_model=list[PublisherGapOut])
async def list_publisher_gaps(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """调研缺口：近 N 天有收入、且任何 alias/app_id 都没命中的 publisher，按累计
    收入降序 top N。把 PUBLISHERS.md 里「数据驱动找缺口」从手 SQL 抬进 UI：
    进页面就看见漏网厂，点「建主体」预填 publisher 名为初始 alias。零 ST 配额。

    必须先于 GET /{entity_id} 声明，否则 'gaps' 会被 int 路径捕获 → 422。
    """
    aliases = (await db.execute(select(PublisherAlias))).scalars().all()
    pinned = (await db.execute(select(PublisherAppId.app_id))).scalars().all()
    alias_kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    alias_squashes = _alias_squash_set(aliases)
    app_id_set = set(pinned)

    # 忽略名单：人工标过「非 SLG 主体」的发行商 / app，不再进缺口（见 PublisherIgnore）。
    ignores = (await db.execute(select(PublisherIgnore))).scalars().all()
    ignore_app_ids = {ig.value for ig in ignores if ig.kind == "app_id"}
    ignore_pub_keys = {ig.value for ig in ignores if ig.kind == "publisher"}

    end = utcnow_naive().date()
    start = end - timedelta(days=days - 1)
    # publisher/name/icon 用 US 优先 + fallback MAX，与 _ranking_pairs / list_publisher_products
    # 同口径：同 app_id 跨市场返回本地化名时，裸 MAX 按 Unicode 排序偏向 CJK 吃掉 Latin 原名。
    # 对 /gaps 尤其要命——CJK publisher 被 _toks 切成空 token → alias/ignore 漏匹 → 已建档/
    # 已忽略的发行商重新冒成缺口（且缺口卡显示日韩名）。见 PUBLISHERS.md「CJK MAX 偏向」。
    us_pub = case((GameRanking.country == "US", GameRanking.publisher))
    us_name = case((GameRanking.country == "US", GameRanking.name))
    us_icon = case((GameRanking.country == "US", GameRanking.icon_url))
    res = await db.execute(
        select(
            GameRanking.app_id,
            func.coalesce(func.max(us_pub), func.max(GameRanking.publisher)).label("pub"),
            func.coalesce(func.max(us_name), func.max(GameRanking.name)).label("name"),
            func.coalesce(func.max(us_icon), func.max(GameRanking.icon_url)).label("icon"),
            func.sum(GameRanking.revenue).label("rev"),
            func.sum(GameRanking.downloads).label("dl"),
        ).where(
            GameRanking.chart_type == CHART_GROSSING,
            GameRanking.date >= start.isoformat(),
            GameRanking.date <= end.isoformat(),
            GameRanking.publisher.is_not(None),
            GameRanking.publisher != "",
        ).group_by(GameRanking.app_id)
    )

    # publisher 归一键（去标点/大小写）→ 桶；同名 publisher 跨 app 合算。
    # 用 normalize 后的 token 序列做键，让 "Kabam Games Ltd." 和 "Kabam Games" 合并。
    by_pub: dict[str, dict] = {}
    for app_id, pub, name, icon, rev, dl in res.all():
        revv = float(rev or 0)
        if revv <= 0:
            continue
        if app_id in app_id_set:
            continue  # 已被 app_id 精确钉
        if app_id in ignore_app_ids:
            continue  # 单品已被人工忽略
        pub_tokens = _toks(pub)
        if _pub_hit(pub_tokens, alias_kw_tokens, alias_squashes):
            continue  # 已被某主体的 alias 命中（含连写 squash 回退）
        if corp_squash(pub_tokens) in ignore_pub_keys:
            continue  # 整个发行商已被人工忽略（非 SLG 巨头）
        key = " ".join(pub_tokens) if pub_tokens else (pub or "").lower()
        if not key:
            continue
        bucket = by_pub.setdefault(key, {"display": pub, "revenue": 0.0, "downloads": 0, "apps": []})
        bucket["revenue"] += revv
        bucket["downloads"] += int(dl or 0)
        bucket["apps"].append((revv, app_id, name, icon))

    items: list[PublisherGapOut] = []
    for b in by_pub.values():
        b["apps"].sort(key=lambda x: -x[0])
        rev, aid, nm, ic = b["apps"][0]
        items.append(PublisherGapOut(
            publisher=b["display"], revenue=b["revenue"], downloads=b["downloads"],
            app_count=len(b["apps"]),
            top_app=PublisherTopProductOut(app_id=aid, name=nm, icon_url=ic),
        ))
    items.sort(key=lambda x: -x.revenue)
    return items[:limit]


# iTunes 反解礼貌限速：按 app id 查是单请求（非多区），比账号同步轻；扫描是显式用户动作。
_SUGGEST_LOOKUP_DELAY_S = 1.0
# 每主体最多反解几个候选 app（pinned 在前、alias 匹配产品按收入降序在后）——挡住某主体
# alias 匹配出几十个产品时的失控解析；通常第一个（旗舰）就解析出账号、命中即停。
_SUGGEST_MAX_APPS_PER_ENTITY = 6


def _ios_suggest_candidates(entity_aliases, pinned_ios: list[str], pairs) -> list[str]:
    """某主体可反解开发者账号的 iOS 数字 app_id 候选，按置信度/收入排序、去重、封顶：
    ① pinned iOS app_id（人工钉=最高置信）在前；
    ② alias 匹配到的产品里的 iOS app_id（slice 2），按收入降序（旗舰先解）补在后。
    matched 用与 /gaps、列表同一套 `_pub_hit`（token 子序列 + squash 回退）判定。"""
    out: list[str] = list(pinned_ios)
    seen = set(out)
    kw_tokens = [tuple(_toks(a.keyword)) for a in entity_aliases if _toks(a.keyword)]
    squashes = _alias_squash_set(entity_aliases)
    if kw_tokens or squashes:
        matched: list[tuple[str, float]] = []
        for app_id, pub, name, icon, rev in pairs:
            if app_id.isdigit() and app_id not in seen and _pub_hit(_toks(pub), kw_tokens, squashes):
                matched.append((app_id, float(rev or 0)))
                seen.add(app_id)
        matched.sort(key=lambda x: -x[1])
        out.extend(app_id for app_id, _ in matched)
    return out[:_SUGGEST_MAX_APPS_PER_ENTITY]


@router.get("/itunes-artist-suggestions", response_model=list[PublisherArtistSuggestionOut])
async def list_itunes_artist_suggestions(
    limit: int = Query(25, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
):
    """雷达覆盖建议：对「未接 iOS 雷达的 is_slg 主体」，从其 **iOS 数字 app_id** 免费反解出
    开发者账号 artistId，给出可一键接入的候选（接入复用 POST /{id}/itunes-artists）。

    候选 app_id 来源（高置信优先）：① pinned iOS app_id（人工钉）；② alias 匹配到的产品里的
    iOS app_id（slice 2——大量真 SLG 单厂没钉 app、只靠 alias 归属，旗舰产品的开发者账号
    就是它的雷达入口）。省 toil：把「找开发者页 → 抄 artistId → 粘进抽屉」自动化成「核对
    entity→artistName → 接入」。解析出的 artist 已被任意主体接入则跳过（artist_id 全局唯一）。
    每主体只取第一个能解析的 app（封顶 `_SUGGEST_MAX_APPS_PER_ENTITY`）、礼貌限速、按 limit
    截断。零 ST 配额。mock 模式不出外网 → 空（本地/测试经 monkeypatch 验证）。

    必须先于 GET /{entity_id} 声明，否则字面量 'itunes-artist-suggestions' 会被 int 路径捕获。
    """
    if settings.USE_MOCK_DATA:
        return []
    from app.services.itunes_releases import resolve_artist_for_app

    entities = (await db.execute(select(PublisherEntity))).scalars().all()
    ios_artists = (await db.execute(
        select(PublisherItunesArtist).where(PublisherItunesArtist.platform == "ios")
    )).scalars().all()
    app_ids = (await db.execute(select(PublisherAppId))).scalars().all()
    aliases = (await db.execute(select(PublisherAlias))).scalars().all()
    pairs = await _ranking_pairs(db)  # (app_id, pub, name, icon, rev)；占用反解前算好

    covered = {a.entity_id for a in ios_artists}
    seen_artist = {a.artist_id for a in ios_artists}  # 已接入的全局占用 → 不再建议
    pinned_by_eid: dict[int, list[str]] = {}
    for a in app_ids:
        if a.app_id.isdigit():  # iOS 数字 id 才能 iTunes 反解（Android 包名不行）
            pinned_by_eid.setdefault(a.entity_id, []).append(a.app_id)
    aliases_by_eid: dict[int, list] = {}
    for a in aliases:
        aliases_by_eid.setdefault(a.entity_id, []).append(a)

    # 雷达目标 = is_slg、未接 iOS 雷达、且有可反解的 iOS app_id（pinned 或 alias 匹配产品）；
    # 资本方/纯控股母体不算。每主体的候选 app_id 列表预算好（pinned + 匹配产品）。
    cands_by_eid: dict[int, list[str]] = {}
    for e in entities:
        if not e.is_slg or e.id in covered:
            continue
        cands = _ios_suggest_candidates(
            aliases_by_eid.get(e.id, []), pinned_by_eid.get(e.id, []), pairs)
        if cands:
            cands_by_eid[e.id] = cands
    targets = [e for e in entities if e.id in cands_by_eid]
    targets.sort(key=lambda e: (e.sort_order, e.id))

    out: list[PublisherArtistSuggestionOut] = []
    lookups = 0
    for e in targets:
        if len(out) >= limit:
            break
        for app_id in cands_by_eid[e.id]:
            if lookups > 0:
                await asyncio.sleep(_SUGGEST_LOOKUP_DELAY_S)
            lookups += 1
            cand = await resolve_artist_for_app(app_id)
            if not cand:
                continue
            if cand["artist_id"] in seen_artist:
                continue  # 该候选的开发者账号已被接入 → 试本主体下一个候选（可能另一未占用账号）
            seen_artist.add(cand["artist_id"])  # 防同一 artist 在本轮被多个主体重复建议
            out.append(PublisherArtistSuggestionOut(
                entity_id=e.id, entity_name=e.name,
                source_app_id=app_id, source_app_name=cand.get("app_name"),
                artist_id=cand["artist_id"], artist_name=cand.get("artist_name"),
            ))
            break  # 每主体只给一条建议
    return out


@router.get("/download-leads", response_model=list[PublisherDownloadLeadOut])
async def list_download_leads(
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """下载榜早期信号：下载榜(免费榜) is_slg=false（白名单未收录）但 genre=Strategy 的新品，
    单列为「待建档新厂线索」给维护者。比 grossing 缺口（已起量）更早——新厂常先软启动、
    买量起量先反映在下载榜装机量，几个月后才进收入榜。把 digest 方案① 的线索搬进 publishers
    页 UI，让维护者随时浏览这条早期 backlog（不止 digest 推一次）。

    数据源 = market_newcomer_log（chart_type=free），免费富化 genre/summary_cn，零 ST。
    扣除缺口忽略名单（与 /gaps 同口径）；跨市场同 app 收敛留最新检出；按检出时间倒序。

    必须先于 GET /{entity_id} 声明，否则字面量 'download-leads' 会被 int 路径捕获 → 422。
    """
    cutoff = utcnow_naive() - timedelta(days=days)
    rows = (await db.execute(
        select(MarketNewcomerLog).where(
            MarketNewcomerLog.chart_type == CHART_FREE,
            MarketNewcomerLog.is_slg.is_(False),
            MarketNewcomerLog.first_detected_at >= cutoff,
        ).order_by(MarketNewcomerLog.first_detected_at.desc())
    )).scalars().all()

    ignores = (await db.execute(select(PublisherIgnore))).scalars().all()
    ignore_app_ids = {ig.value for ig in ignores if ig.kind == "app_id"}
    ignore_pub_keys = {ig.value for ig in ignores if ig.kind == "publisher"}

    # 跨市场同 app 收敛成一行：留最新检出做展示锚（市场/名次/时间），富化字段（genre/摘要/
    # 图标/商店）从任意有值的行回填——同 app 不同区富化可能有缺。rows 已按检出时间倒序。
    by_app: dict[str, dict] = {}
    for r in rows:
        rep = by_app.get(r.app_id)
        if rep is None:
            by_app[r.app_id] = {"row": r, "genre": r.genre, "summary_cn": r.summary_cn,
                                "store_url": r.store_url, "icon_url": r.icon_url}
        else:
            for k in ("genre", "summary_cn", "store_url", "icon_url"):
                if not rep[k] and getattr(r, k):
                    rep[k] = getattr(r, k)

    out: list[PublisherDownloadLeadOut] = []
    for rep in by_app.values():  # 已按检出时间倒序（最新线索在前）
        r = rep["row"]
        if r.is_reentry:
            continue  # 回归老面孔不是新厂线索
        if not rep["genre"] or "strateg" not in rep["genre"].lower():
            continue  # 只要疑似 SLG（genre 含 Strategy），压掉休闲噪声
        if r.app_id in ignore_app_ids:
            continue
        if corp_squash(_toks(r.publisher)) in ignore_pub_keys:
            continue
        out.append(PublisherDownloadLeadOut(
            app_id=r.app_id, name=r.name, publisher=r.publisher, genre=rep["genre"],
            summary_cn=rep["summary_cn"], icon_url=rep["icon_url"], store_url=rep["store_url"],
            country=r.country, platform=r.platform, rank=r.rank,
            first_detected_at=r.first_detected_at.isoformat() if r.first_detected_at else None,
        ))
        if len(out) >= limit:
            break
    return out


# ── 缺口忽略名单：把已知非 SLG 巨头从 /gaps 里剔掉，让缺口收敛到可操作信号 ──
# 这些端点都在 publisher 前缀下、且路径段是字面量 'ignores'，必须**先于**
# GET /{entity_id} 声明，否则会被 int 路径捕获 → 422（同 gaps）。

@router.get("/ignores", response_model=list[PublisherIgnoreOut])
async def list_publisher_ignores(db: AsyncSession = Depends(get_db)):
    """列出全部缺口忽略条目（最新在前），供前端展示「已忽略」+ 恢复。零 ST 配额。"""
    rows = (await db.execute(
        select(PublisherIgnore).order_by(PublisherIgnore.created_at.desc())
    )).scalars().all()
    return rows


@router.post("/ignores", response_model=PublisherIgnoreOut, status_code=201)
async def create_publisher_ignore(data: PublisherIgnoreCreate, db: AsyncSession = Depends(get_db)):
    """新增忽略。publisher 粒度：raw_value 归一成 corp_squash 键存储、原串落 label；
    app_id 粒度：原样存。已存在（kind+value 唯一）→ 幂等返回旧条目，不报错。"""
    if data.kind == "publisher":
        value = corp_squash(_toks(data.raw_value))
        if not value:
            raise HTTPException(422, "publisher 名归一后为空，无法忽略")
    else:  # app_id
        value = data.raw_value
    existing = (await db.execute(
        select(PublisherIgnore).where(
            PublisherIgnore.kind == data.kind, PublisherIgnore.value == value
        )
    )).scalar_one_or_none()
    if existing:
        return existing
    row = PublisherIgnore(
        kind=data.kind, value=value,
        label=(data.label or data.raw_value).strip() or None,
        note=data.note,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/ignores/{ignore_id}")
async def delete_publisher_ignore(ignore_id: int, db: AsyncSession = Depends(get_db)):
    """恢复（取消忽略）：删除一条忽略条目，对应发行商/app 下次会重新进缺口。"""
    row = (await db.execute(
        select(PublisherIgnore).where(PublisherIgnore.id == ignore_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "忽略条目不存在")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


@router.get("/{entity_id}", response_model=PublisherEntityOut)
async def get_publisher(entity_id: int, db: AsyncSession = Depends(get_db)):
    e = await _get_entity_or_404(entity_id, db)
    aliases, app_ids = await _children(entity_id, db)
    sources = await _sources(entity_id, db)
    parents, children = await _relations(entity_id, db)
    pairs = await _ranking_pairs_cached(db)
    rank_by_app = await _rank_by_app_cached(db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    count, top, best_rank, best_market = _match_for_entity(
        pairs, kw_tokens, {a.app_id for a in app_ids},
        await _itunes_products(e.id, db), rank_by_app, _alias_squash_set(aliases))
    return _build_out(e, aliases, app_ids, sources, parents, children, count,
                      itunes_artists=await _itunes_artists(e.id, db), top_products=top,
                      best_rank=best_rank, best_market=best_market)


@router.put("/{entity_id}", response_model=PublisherEntityOut)
async def update_publisher(entity_id: int, data: PublisherEntityUpdate, db: AsyncSession = Depends(get_db)):
    e = await _get_entity_or_404(entity_id, db)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(e, k, v)
    await db.commit()
    await db.refresh(e)
    await load_index_from_db()  # is_slg 字段虽不入索引，统一刷新保持简单
    aliases, app_ids = await _children(entity_id, db)
    sources = await _sources(entity_id, db)
    parents, children = await _relations(entity_id, db)
    pairs = await _ranking_pairs_cached(db)
    rank_by_app = await _rank_by_app_cached(db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    count, top, best_rank, best_market = _match_for_entity(
        pairs, kw_tokens, {a.app_id for a in app_ids},
        await _itunes_products(e.id, db), rank_by_app, _alias_squash_set(aliases))
    return _build_out(e, aliases, app_ids, sources, parents, children, count,
                      itunes_artists=await _itunes_artists(e.id, db), top_products=top,
                      best_rank=best_rank, best_market=best_market)


@router.delete("/{entity_id}")
async def delete_publisher(entity_id: int, db: AsyncSession = Depends(get_db)):
    e = await _get_entity_or_404(entity_id, db)
    # SQLite 默认不强制 FK 级联，应用层显式删子行。
    from app.models.publisher import PublisherItunesApp
    await db.execute(sa_delete(PublisherAlias).where(PublisherAlias.entity_id == entity_id))
    await db.execute(sa_delete(PublisherAppId).where(PublisherAppId.entity_id == entity_id))
    await db.execute(sa_delete(PublisherSource).where(PublisherSource.entity_id == entity_id))
    await db.execute(sa_delete(PublisherItunesApp).where(PublisherItunesApp.entity_id == entity_id))
    await db.execute(sa_delete(PublisherItunesArtist).where(PublisherItunesArtist.entity_id == entity_id))
    await db.execute(sa_delete(PublisherRelation).where(
        or_(PublisherRelation.parent_id == entity_id, PublisherRelation.child_id == entity_id)
    ))
    await db.delete(e)
    await db.commit()
    await load_index_from_db()
    return {"message": "deleted", "id": entity_id}


# ── 子资源：海外发行马甲 ───────────────────────────────────────────────────

@router.post("/{entity_id}/aliases", response_model=PublisherAliasOut, status_code=201)
async def add_alias(entity_id: int, data: PublisherAliasCreate, db: AsyncSession = Depends(get_db)):
    await _get_entity_or_404(entity_id, db)
    keyword = data.keyword.strip()
    dup = (await db.execute(
        select(PublisherAlias).where(
            PublisherAlias.entity_id == entity_id, PublisherAlias.keyword == keyword
        )
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="该主体下已有同名马甲")
    a = PublisherAlias(entity_id=entity_id, keyword=keyword, label=data.label)
    db.add(a)
    await db.commit()
    await db.refresh(a)
    await load_index_from_db()
    return PublisherAliasOut.model_validate(a)


@router.delete("/{entity_id}/aliases/{alias_id}")
async def delete_alias(entity_id: int, alias_id: int, db: AsyncSession = Depends(get_db)):
    a = (await db.execute(
        select(PublisherAlias).where(
            PublisherAlias.id == alias_id, PublisherAlias.entity_id == entity_id
        )
    )).scalar_one_or_none()
    if a:
        await db.delete(a)
        await db.commit()
        await load_index_from_db()
    return {"message": "deleted"}


# ── 子资源：关注 app_id ────────────────────────────────────────────────────

@router.post("/{entity_id}/app-ids", response_model=PublisherAppIdOut, status_code=201)
async def add_app_id(entity_id: int, data: PublisherAppIdCreate, db: AsyncSession = Depends(get_db)):
    await _get_entity_or_404(entity_id, db)
    app_id = data.app_id.strip()
    dup = (await db.execute(
        select(PublisherAppId).where(
            PublisherAppId.entity_id == entity_id, PublisherAppId.app_id == app_id
        )
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="该主体下已钉同一 app_id")
    a = PublisherAppId(entity_id=entity_id, app_id=app_id, note=data.note)
    db.add(a)
    await db.commit()
    await db.refresh(a)
    await load_index_from_db()
    return PublisherAppIdOut.model_validate(a)


@router.delete("/{entity_id}/app-ids/{app_id_row_id}")
async def delete_app_id(entity_id: int, app_id_row_id: int, db: AsyncSession = Depends(get_db)):
    a = (await db.execute(
        select(PublisherAppId).where(
            PublisherAppId.id == app_id_row_id, PublisherAppId.entity_id == entity_id
        )
    )).scalar_one_or_none()
    if a:
        await db.delete(a)
        await db.commit()
        await load_index_from_db()
    return {"message": "deleted"}


# ── 子资源：App Store 开发者账号（iTunes artistId）────────────────────────

@router.post("/{entity_id}/itunes-artists", response_model=PublisherItunesArtistOut, status_code=201)
async def add_itunes_artist(entity_id: int, data: PublisherItunesArtistCreate, db: AsyncSession = Depends(get_db)):
    """给主体挂一个应用商店开发者账号（platform='ios' 为 iTunes artistId，
    'gp' 为 Google Play 开发者页 id）。artist_id 全局唯一（一个账号只归一个主体）。
    不影响 is_slg 判定，不刷新内存索引；清单同步由调度 job / 手动端点负责。"""
    await _get_entity_or_404(entity_id, db)
    dup = (await db.execute(
        select(PublisherItunesArtist).where(PublisherItunesArtist.artist_id == data.artist_id)
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="该 artist_id 已挂在某主体下")
    a = PublisherItunesArtist(entity_id=entity_id, artist_id=data.artist_id,
                              platform=data.platform, label=data.label)
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return PublisherItunesArtistOut.model_validate(a)


@router.delete("/{entity_id}/itunes-artists/{artist_row_id}")
async def delete_itunes_artist(entity_id: int, artist_row_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.publisher import PublisherItunesApp
    a = (await db.execute(
        select(PublisherItunesArtist).where(
            PublisherItunesArtist.id == artist_row_id,
            PublisherItunesArtist.entity_id == entity_id,
        )
    )).scalar_one_or_none()
    if a:
        # 连带删清单快照（SQLite 不强制 FK 级联）
        await db.execute(sa_delete(PublisherItunesApp).where(
            PublisherItunesApp.artist_row_id == artist_row_id))
        await db.delete(a)
        await db.commit()
    return {"message": "deleted"}


# ── 子资源：调研出处（一手源溯源）─────────────────────────────────────────

@router.post("/{entity_id}/sources", response_model=PublisherSourceOut, status_code=201)
async def add_source(entity_id: int, data: PublisherSourceCreate, db: AsyncSession = Depends(get_db)):
    """给主体加一条调研出处。source_type 非法 → 422（pydantic 校验）。
    溯源源不影响 is_slg 判定，故不刷新内存索引。"""
    await _get_entity_or_404(entity_id, db)
    s = PublisherSource(
        entity_id=entity_id, url=data.url.strip(), title=data.title,
        source_type=data.source_type, confidence=data.confidence,
        as_of=data.as_of, note=data.note,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _source_out(s)


@router.delete("/{entity_id}/sources/{source_id}")
async def delete_source(entity_id: int, source_id: int, db: AsyncSession = Depends(get_db)):
    s = (await db.execute(
        select(PublisherSource).where(
            PublisherSource.id == source_id, PublisherSource.entity_id == entity_id
        )
    )).scalar_one_or_none()
    if s:
        await db.delete(s)
        await db.commit()
    return {"message": "deleted"}


# ── 子资源：股权/母子关系 ──────────────────────────────────────────────────

@router.post("/{entity_id}/relations", response_model=PublisherRelationLinkOut, status_code=201)
async def add_relation(entity_id: int, data: PublisherRelationCreate, db: AsyncSession = Depends(get_db)):
    """从本主体视角加一条股权关系。counterpart_role='parent' = 对方是本主体母公司；
    'child' = 对方是本主体子公司。禁自环、禁重复 (parent,child) 对。"""
    await _get_entity_or_404(entity_id, db)
    if data.counterpart_id == entity_id:
        raise HTTPException(status_code=400, detail="不能与自身建立股权关系")
    await _get_entity_or_404(data.counterpart_id, db)  # 对方必须存在

    if data.counterpart_role == "parent":
        parent_id, child_id = data.counterpart_id, entity_id
    else:  # 'child'
        parent_id, child_id = entity_id, data.counterpart_id

    dup = (await db.execute(
        select(PublisherRelation).where(
            PublisherRelation.parent_id == parent_id, PublisherRelation.child_id == child_id
        )
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="该股权关系已存在")

    rel = PublisherRelation(
        parent_id=parent_id, child_id=child_id,
        relation_type=data.relation_type, stake_pct=data.stake_pct, note=data.note,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    # 从本主体视角返回（对方 = counterpart_id）
    name = (await db.execute(
        select(PublisherEntity.name).where(PublisherEntity.id == data.counterpart_id)
    )).scalar_one()
    return _rel_link(rel, data.counterpart_id, {data.counterpart_id: name})


@router.delete("/{entity_id}/relations/{relation_id}")
async def delete_relation(entity_id: int, relation_id: int, db: AsyncSession = Depends(get_db)):
    rel = (await db.execute(
        select(PublisherRelation).where(
            PublisherRelation.id == relation_id,
            or_(PublisherRelation.parent_id == entity_id, PublisherRelation.child_id == entity_id),
        )
    )).scalar_one_or_none()
    if rel:
        await db.delete(rel)
        await db.commit()
    return {"message": "deleted"}


# ── 旗下产品聚合 ───────────────────────────────────────────────────────────

@router.get("/{entity_id}/products", response_model=list[PublisherProductOut])
async def list_publisher_products(
    entity_id: int,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """主体旗下产品：窗口内跨已监测市场合计下载/收入，按收入降序。零 ST 配额。

    跨平台 sibling 去重（同发行商 + 名字 prefix 子序列匹配 ≥5）：iOS+Android 同款不再
    重复占行；下载/收入按组合计。同 _compute_all_matches/_match_for_entity 口径。
    """
    await _get_entity_or_404(entity_id, db)
    aliases, app_ids = await _children(entity_id, db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    alias_squashes = _alias_squash_set(aliases)
    app_id_set = {a.app_id for a in app_ids}

    end = utcnow_naive().date()
    start = end - timedelta(days=days - 1)
    # name/publisher/icon 用 US 优先：同 iOS app_id 多市场返回本地化名时拿到 Latin 原名，
    # sibling 跨平台合并才能稳定匹配；downloads/revenue 仍是窗口跨市场 SUM。
    us_name = case((GameRanking.country == "US", GameRanking.name))
    us_pub  = case((GameRanking.country == "US", GameRanking.publisher))
    us_icon = case((GameRanking.country == "US", GameRanking.icon_url))
    res = await db.execute(
        select(
            GameRanking.app_id,
            func.coalesce(func.max(us_name), func.max(GameRanking.name)).label("name"),
            func.coalesce(func.max(us_pub), func.max(GameRanking.publisher)).label("publisher"),
            func.coalesce(func.max(us_icon), func.max(GameRanking.icon_url)).label("icon_url"),
            func.sum(GameRanking.downloads).label("downloads"),
            func.sum(GameRanking.revenue).label("revenue"),
        ).where(
            GameRanking.chart_type == CHART_GROSSING,
            GameRanking.date >= start.isoformat(),
            GameRanking.date <= end.isoformat(),
        ).group_by(GameRanking.app_id)
    )

    # 先收集 per-app 行（含未去重的 (rev/dl/matched_by/publisher) 完整信息），
    # 再过 _dedup_siblings 合成组级 PublisherProductOut。
    matched_dict: dict[str, tuple[float, str, str, str | None]] = {}  # for dedup
    raw_per_aid: dict[str, dict] = {}  # 给组级合计 downloads / 找代表 matched_by/publisher 用
    for r in res.all():
        if r.app_id in app_id_set:
            matched_by = "app_id"
        elif _pub_hit(_toks(r.publisher), kw_tokens, alias_squashes):
            matched_by = "alias"
        else:
            continue
        rev = float(r.revenue or 0)
        matched_dict[r.app_id] = (rev, r.name, r.icon_url, r.publisher)
        raw_per_aid[r.app_id] = {
            "downloads": int(r.downloads or 0), "publisher": r.publisher,
            "matched_by": matched_by, "genre": None,
        }
    # 并入雷达 itunes_apps（开发者账号下的 app = 旗下产品，含未上榜软启动新品），
    # 与卡片 product_count/top_products 同口径。雷达 publisher=None 故 sibling 跳过它，
    # 保留为独立组（同款跨平台时仍按名字字面去重，见 seen_names 兜底）。
    seen_names = {(v[1] or "").strip().lower() for v in matched_dict.values() if v[1]}
    for track_id, name, artwork, genre in await _itunes_products(entity_id, db):
        if track_id in matched_dict:
            continue
        key = (name or "").strip().lower()
        if key and key in seen_names:
            continue
        if key:
            seen_names.add(key)
        matched_dict[track_id] = (0, name, artwork, None)
        raw_per_aid[track_id] = {"downloads": 0, "publisher": None, "matched_by": "radar", "genre": genre}

    groups = _dedup_siblings(matched_dict)
    items: list[PublisherProductOut] = []
    for g in groups:
        rep_aid = g["app_id"]
        meta = raw_per_aid.get(rep_aid, {"downloads": 0, "publisher": None, "matched_by": "alias", "genre": None})
        total_dl = sum(raw_per_aid.get(aid, {}).get("downloads", 0) for aid in g["member_app_ids"])
        items.append(PublisherProductOut(
            app_id=rep_aid, name=g["name"], publisher=meta["publisher"], icon_url=g["icon_url"],
            downloads=total_dl, revenue=g["revenue"], matched_by=meta["matched_by"], genre=meta["genre"],
        ))
    items.sort(key=lambda x: -x.revenue)
    return items
