"""厂商主体（publisher_entities）CRUD + 「主体→旗下产品」聚合。

主体 / 海外发行马甲 / 关注 app_id 三层在看板上维护，是 is_slg 判定的唯一数据源；
任何写操作后调 load_index_from_db() 刷新 slg_publishers 的内存索引，让榜单过滤 /
异动检测即时生效，无需重启。

「旗下产品」是查询态聚合——用主体的马甲 keyword 对 game_rankings.publisher 做 token
子序列匹配 + app_ids 精确匹配，跨已监测市场窗口合计下载/收入，零 ST 配额、纯本地库。
"""
import re
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, delete as sa_delete
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
    PublisherTopProductOut,
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
    供批量算各主体旗下产品数 + 取折叠态图标锚点（按收入降序的 top3）。零 ST 配额。"""
    res = await db.execute(
        select(
            GameRanking.app_id,
            func.max(GameRanking.publisher),
            func.max(GameRanking.name),
            func.max(GameRanking.icon_url),
            func.max(GameRanking.revenue),
        ).group_by(GameRanking.app_id)
    )
    return [(app_id, pub, name, icon, rev) for app_id, pub, name, icon, rev in res.all()]


def _match_for_entity(pairs, alias_kw_tokens, app_id_set, itunes_products=()):
    """返回 (旗下产品数, 按收入降序的 top3 PublisherTopProductOut)。

    两个来源并集：
    - **榜单 game_rankings**（pairs）：app_id 精确钉 或 alias token 命中代表 publisher；带收入。
    - **雷达 itunes_apps**（itunes_products，按 entity_id 直挂）：开发者账号下的 app 就是
      旗下产品，含**未上榜的软启动新品**（如新厂商主打新品）——这类产品永远进不了
      榜单聚合，只能从雷达补。无收入。

    去重：榜单命中按 app_id；雷达产品跳过已在榜单命中集的 app_id，并按 name 去同名
    （Top Lords 的 iOS / GP 双平台只算一款）。top3 优先有收入的（榜单来源），雷达
    产品收入视为 0 排其后。"""
    matched: dict[str, tuple[float, str, str]] = {}  # app_id -> (revenue, name, icon)
    for app_id, pub, name, icon, rev in pairs:
        hit = app_id in app_id_set or (
            alias_kw_tokens and any(_kw_hit(_toks(pub), kt) for kt in alias_kw_tokens))
        if hit:
            matched[app_id] = (rev or 0, name, icon)
    seen_names = {(n or "").strip().lower() for _, n, _ in matched.values() if n}
    for track_id, name, artwork, _genre in itunes_products:
        if track_id in matched:
            continue
        key = (name or "").strip().lower()
        if key and key in seen_names:
            continue  # 同名跨平台去重（雷达里 iOS+GP 各一条同款）
        if key:
            seen_names.add(key)
        matched[track_id] = (0, name, artwork)
    ordered = sorted(matched.items(), key=lambda kv: -kv[1][0])
    top = [PublisherTopProductOut(app_id=aid, name=v[1], icon_url=v[2]) for aid, v in ordered[:3]]
    return len(matched), top


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
               product_count: int | None, itunes_artists=(), top_products=()) -> PublisherEntityOut:
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
    pairs = await _ranking_pairs(db)
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

    out = []
    for e in entities:
        aliases = by_alias.get(e.id, [])
        app_ids = by_appid.get(e.id, [])
        sources = by_source.get(e.id, [])
        kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
        app_id_set = {a.app_id for a in app_ids}
        count, top = _match_for_entity(pairs, kw_tokens, app_id_set, itunes_by_entity.get(e.id, []))
        out.append(_build_out(
            e, aliases, app_ids, sources,
            by_parents.get(e.id, []), by_children.get(e.id, []),
            count, itunes_artists=by_artist.get(e.id, []), top_products=top,
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
    pairs = await _ranking_pairs(db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    count, top = _match_for_entity(pairs, kw_tokens, {a.app_id for a in app_ids},
                                   await _itunes_products(e.id, db))
    return _build_out(e, aliases, app_ids, sources, parents, children, count,
                      itunes_artists=await _itunes_artists(e.id, db), top_products=top)


@router.get("/{entity_id}", response_model=PublisherEntityOut)
async def get_publisher(entity_id: int, db: AsyncSession = Depends(get_db)):
    e = await _get_entity_or_404(entity_id, db)
    aliases, app_ids = await _children(entity_id, db)
    sources = await _sources(entity_id, db)
    parents, children = await _relations(entity_id, db)
    pairs = await _ranking_pairs(db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    count, top = _match_for_entity(pairs, kw_tokens, {a.app_id for a in app_ids},
                                   await _itunes_products(e.id, db))
    return _build_out(e, aliases, app_ids, sources, parents, children, count,
                      itunes_artists=await _itunes_artists(e.id, db), top_products=top)


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
    pairs = await _ranking_pairs(db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    count, top = _match_for_entity(pairs, kw_tokens, {a.app_id for a in app_ids},
                                   await _itunes_products(e.id, db))
    return _build_out(e, aliases, app_ids, sources, parents, children, count,
                      itunes_artists=await _itunes_artists(e.id, db), top_products=top)


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
    """主体旗下产品：窗口内跨已监测市场合计下载/收入，按收入降序。零 ST 配额。"""
    await _get_entity_or_404(entity_id, db)
    aliases, app_ids = await _children(entity_id, db)
    kw_tokens = [tuple(_toks(a.keyword)) for a in aliases if _toks(a.keyword)]
    app_id_set = {a.app_id for a in app_ids}

    end = utcnow_naive().date()
    start = end - timedelta(days=days - 1)
    res = await db.execute(
        select(
            GameRanking.app_id,
            func.max(GameRanking.name).label("name"),
            func.max(GameRanking.publisher).label("publisher"),
            func.max(GameRanking.icon_url).label("icon_url"),
            func.sum(GameRanking.downloads).label("downloads"),
            func.sum(GameRanking.revenue).label("revenue"),
        ).where(
            GameRanking.date >= start.isoformat(),
            GameRanking.date <= end.isoformat(),
        ).group_by(GameRanking.app_id)
    )
    items: list[PublisherProductOut] = []
    for r in res.all():
        if r.app_id in app_id_set:
            matched = "app_id"
        elif kw_tokens and _toks(r.publisher) and any(_kw_hit(_toks(r.publisher), kt) for kt in kw_tokens):
            matched = "alias"
        else:
            continue
        items.append(PublisherProductOut(
            app_id=r.app_id, name=r.name, publisher=r.publisher, icon_url=r.icon_url,
            downloads=int(r.downloads or 0), revenue=float(r.revenue or 0), matched_by=matched,
        ))
    # 并入雷达 itunes_apps（开发者账号下的 app = 旗下产品，含未上榜软启动新品），
    # 与卡片 product_count/top_products 同口径——否则卡片有数、抽屉为空，自相矛盾。
    matched_ids = {i.app_id for i in items}
    seen_names = {(i.name or "").strip().lower() for i in items if i.name}
    for track_id, name, artwork, genre in await _itunes_products(entity_id, db):
        if track_id in matched_ids:
            continue
        key = (name or "").strip().lower()
        if key and key in seen_names:
            continue  # 同名跨平台去重（iOS+GP 同款）
        if key:
            seen_names.add(key)
        items.append(PublisherProductOut(
            app_id=track_id, name=name, publisher=None, icon_url=artwork,
            downloads=0, revenue=0, matched_by="radar", genre=genre,
        ))
    items.sort(key=lambda x: -x.revenue)
    return items
