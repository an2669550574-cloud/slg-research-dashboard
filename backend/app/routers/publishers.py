"""厂商主体（publisher_entities）CRUD + 「主体→旗下产品」聚合。

主体 / 海外发行马甲 / 关注 app_id 三层在看板上维护，是 is_slg 判定的唯一数据源；
任何写操作后调 load_index_from_db() 刷新 slg_publishers 的内存索引，让榜单过滤 /
异动检测即时生效，无需重启。

「旗下产品」是查询态聚合——用主体的马甲 keyword 对 game_rankings.publisher 做 token
子序列匹配 + app_ids 精确匹配，跨已监测市场窗口合计下载/收入，零 ST 配额、纯本地库。
"""
import re
import time
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_, case, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, utcnow_naive
from app.models.publisher import (
    PublisherEntity, PublisherAlias, PublisherAppId, PublisherSource, PublisherRelation,
    PublisherItunesArtist, PublisherItunesApp,
)
from app.models.game import GameRanking
from app.schemas import (
    PublisherEntityOut, PublisherEntityCreate, PublisherEntityUpdate,
    PublisherAliasOut, PublisherAliasCreate,
    PublisherAppIdOut, PublisherAppIdCreate,
    PublisherSourceOut, PublisherSourceCreate,
    PublisherItunesArtistOut, PublisherItunesArtistCreate,
    PublisherRelationCreate, PublisherRelationLinkOut, PublisherProductOut,
    PublisherTopProductOut, PublisherGapOut, PublisherHealthOut,
)
from app.services.slg_publishers import load_index_from_db
from app.services.provenance import is_primary, provenance_tier

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
        ).group_by(GameRanking.app_id)
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


async def _rank_by_app(db: AsyncSession) -> dict[str, tuple[int, str]]:
    """{app_id: (跨市场最佳名次, 命中市场如 "JP/android")}。只看各 (国家,平台) **最新一期**
    快照——反映「当前畅销」而非历史最好，供「按畅销榜名次」排序。零 ST 配额、纯本地库。"""
    latest = (
        select(GameRanking.country, GameRanking.platform,
               func.max(GameRanking.date).label("md"))
        .group_by(GameRanking.country, GameRanking.platform)
    ).subquery()
    rows = (await db.execute(
        select(GameRanking.app_id, GameRanking.country, GameRanking.platform, GameRanking.rank)
        .join(latest, and_(GameRanking.country == latest.c.country,
                           GameRanking.platform == latest.c.platform,
                           GameRanking.date == latest.c.md))
    )).all()
    best: dict[str, tuple[int, str]] = {}
    for app_id, country, platform, rank in rows:
        if rank is None:
            continue
        cur = best.get(app_id)
        if cur is None or rank < cur[0]:
            best[app_id] = (rank, f"{country}/{platform}")
    return best


async def _rank_by_app_cached(db: AsyncSession) -> dict[str, tuple[int, str]]:
    global _rank_cache
    now = time.monotonic()
    if _rank_cache and now - _rank_cache[0] < _PAIRS_CACHE_TTL:
        return _rank_cache[1]
    rb = await _rank_by_app(db)
    _rank_cache = (now, rb)
    return rb


def _match_for_entity(pairs, alias_kw_tokens, app_id_set, itunes_products=(), rank_by_app=None):
    """返回 (旗下产品数, 按收入降序的 top3 PublisherTopProductOut, 最佳名次, 命中市场)。
    rank_by_app 给定时算旗下产品在各市场最新快照的最小名次（best_rank/market），否则两者为 None。

    两个来源并集：
    - **榜单 game_rankings**（pairs）：app_id 精确钉 或 alias token 命中代表 publisher；带收入。
    - **雷达 itunes_apps**（itunes_products，按 entity_id 直挂）：开发者账号下的 app 就是
      旗下产品，含**未上榜的软启动新品**（如新厂商主打新品）——这类产品永远进不了
      榜单聚合，只能从雷达补。无收入、publisher 未知，故不参与跨平台 sibling 合并。

    跨平台去重：matched 按 app_id 聚集后，过 _dedup_siblings 把同 publisher 同款 iOS+Android
    合并成一组（同款规则 = sibling_match 既定规则）。product_count = 去重后组数，top3 = 组级
    收入 top3。best_rank 仍按所有 member app_id 取最小（同款多平台谁名次高用谁）。"""
    matched: dict[str, tuple[float, str, str, str | None]] = {}  # app_id -> (revenue, name, icon, publisher)
    for app_id, pub, name, icon, rev in pairs:
        hit = app_id in app_id_set or (
            alias_kw_tokens and any(_kw_hit(_toks(pub), kt) for kt in alias_kw_tokens))
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
        matched[track_id] = (0, name, artwork, None)  # publisher 未知 → sibling 合并跳过
    groups = _dedup_siblings(matched)
    top = [PublisherTopProductOut(app_id=g["app_id"], name=g["name"], icon_url=g["icon_url"]) for g in groups[:3]]
    best_rank: int | None = None
    best_market: str | None = None
    if rank_by_app:
        for g in groups:
            for aid in g["member_app_ids"]:
                hit = rank_by_app.get(aid)
                if hit and (best_rank is None or hit[0] < best_rank):
                    best_rank, best_market = hit
    return len(groups), top, best_rank, best_market


_NORM_FOR_SIBLING = re.compile(r"[^a-z0-9]")


def _norm_for_sibling(s: str | None) -> str:
    """与 sibling_match.normalize_ident 同口径：去大小写 + 删非字母数字（含 CJK / 标点 / 空格）。"""
    return _NORM_FOR_SIBLING.sub("", (s or "").lower())


def _dedup_siblings(matched: dict[str, tuple]) -> list[dict]:
    """跨平台同款游戏去重（同发行商 + 名字 prefix 子序列匹配 ≥5）。

    输入 matched: {app_id: (revenue, name, icon, publisher_or_None)}
    输出每组一行：{app_id (代表=收入最高的 member), name (本组里最长 Latin 名，否则原名),
                  icon_url, revenue (本组合计), downloads (始终 0，调用方按需补), member_app_ids}
    按 revenue 降序排好。

    规则同 services/sibling_match.py（详情页跨平台合并复用）：normalize 后
    publisher 相同 + 一方 name 是另一方的 prefix 且短端 ≥5 字符 = 同款。这能合并
    iOS+Android 同 publisher 同名（"Whiteout Survival" / "Whiteout Survival" 完全相同 →
    norm 后等价 → prefix 自匹配）、以及 "Top War" + "Top War: Battle Game" 这类带后缀
    差异的跨平台 listing。CJK-only 本地化名（norm 后为空）不参与匹配 → 保留为独立组，
    与原行为兼容。雷达产品 publisher=None 时也保留为独立组（不知 publisher 不敢合并）。
    """
    if not matched:
        return []
    items = list(matched.items())
    n = len(items)
    enriched = [(aid, rev, name, icon, pub, _norm_for_sibling(pub), _norm_for_sibling(name))
                for aid, (rev, name, icon, pub) in items]
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
        npi, nni = enriched[i][5], enriched[i][6]
        if not npi or not nni:
            continue  # 无 publisher 或纯 CJK 名 → 不参与合并
        for j in range(i + 1, n):
            npj, nnj = enriched[j][5], enriched[j][6]
            if not npj or npj != npi:
                continue  # 必须同 publisher（normalize 后等价）
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
        rep_aid, rep_rev, rep_name, rep_icon, _, _, _ = members[0]
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
    for eid, aliases in by_alias.items():
        for a in aliases:
            t = tuple(_toks(a.keyword))
            if t:
                alias_idx.setdefault(t[0], []).append((t, eid))
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
            m[track_id] = (0, iname, artwork, None)  # publisher 未知 → sibling 合并跳过
        groups = _dedup_siblings(m)
        top = [PublisherTopProductOut(app_id=g["app_id"], name=g["name"], icon_url=g["icon_url"]) for g in groups[:3]]
        best_rank: int | None = None
        best_market: str | None = None
        if rank_by_app:
            for g in groups:
                for aid in g["member_app_ids"]:
                    hit = rank_by_app.get(aid)
                    if hit and (best_rank is None or hit[0] < best_rank):
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
    for a in data.aliases:
        db.add(PublisherAlias(entity_id=e.id, keyword=a.keyword.strip(), label=a.label))
    for ap in data.app_ids:
        db.add(PublisherAppId(entity_id=e.id, app_id=ap.app_id.strip(), note=ap.note))
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
        await _itunes_products(e.id, db), rank_by_app)
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
    app_id_set = set(pinned)

    end = utcnow_naive().date()
    start = end - timedelta(days=days - 1)
    res = await db.execute(
        select(
            GameRanking.app_id,
            func.max(GameRanking.publisher).label("pub"),
            func.max(GameRanking.name).label("name"),
            func.max(GameRanking.icon_url).label("icon"),
            func.sum(GameRanking.revenue).label("rev"),
            func.sum(GameRanking.downloads).label("dl"),
        ).where(
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
        pub_tokens = _toks(pub)
        if alias_kw_tokens and any(_kw_hit(pub_tokens, kt) for kt in alias_kw_tokens):
            continue  # 已被某主体的 alias 命中
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
        await _itunes_products(e.id, db), rank_by_app)
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
        await _itunes_products(e.id, db), rank_by_app)
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
    a = PublisherAlias(entity_id=entity_id, keyword=data.keyword.strip(), label=data.label)
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
    a = PublisherAppId(entity_id=entity_id, app_id=data.app_id.strip(), note=data.note)
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
        elif kw_tokens and _toks(r.publisher) and any(_kw_hit(_toks(r.publisher), kt) for kt in kw_tokens):
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
