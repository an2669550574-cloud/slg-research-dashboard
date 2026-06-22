"""跨平台同款游戏识别：iOS 与 Android 的 app_id 不同，但同款游戏在
`game_rankings` 里会以两套 app_id 各自累积数据。详情页/聚合视图想以"游戏"
为单位展示时，需要把这些 app_id 视为同一族。

与前端 `lib/aggregateMerge.ts` **同款规则**——同 publisher + 名字一方是
另一方的规范化前缀且短的 ≥ 5 字符 → 同款。

"同 publisher" 用两段判断（任一命中即同）：
1. 规范化字符串等同（覆盖 "Century Games Pte. Ltd." vs "Century Games PTE. LTD."
   这类只差大小写/标点的常见 case）；
2. **两个 publisher 字符串通过 `publisher_aliases` 表都映射到同一个 entity**——
   覆盖 "TOP GAMES INC." vs "TG Inc."、"InnoGames GmbH" vs "InnoGames"、
   "RiverGame" vs "River Game HK Limited" 这类同公司不同法人/简写。alias 表
   已是建档时的 ground truth；不查 alias 表会把这些同款拒之门外。

名字归一时优先取 US 行（country='US'）的，因为它通常是开发商提交的英文原名；
KR/JP 行往往是本地化（"킹샷:Kingshot"/"ホワイトアウト・サバイバル"），规范化
后跟其它平台的英文版不前缀匹配。

仍然没有 entity 映射的 publisher（独立小厂、未建档主体），保留原"normalize 后等同"
的保守判断——找不到锚就只返自己，绝不跨未知边界乱合。
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GameRanking
from app.models.publisher import PublisherAlias
from app.services.name_match import corp_squash

_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_ident(s: Optional[str]) -> str:
    """去大小写、删非字母数字。'Last War:Survival Game' → 'lastwarsurvivalgame'。"""
    return _NORM_RE.sub("", (s or "").lower())


def _toks(s: Optional[str]) -> list[str]:
    """同 routers/publishers._toks / services/slg_publishers._toks：小写 + 按非字母数字分词。"""
    return [t for t in _NORM_RE.split((s or "").lower()) if t]


def _kw_hit(pub_tokens: list[str], kw_tokens: tuple[str, ...]) -> bool:
    """kw_tokens 作为**连续子序列**出现在 pub_tokens 即命中（同 routers/publishers._kw_hit）。"""
    n = len(kw_tokens)
    if n == 0:
        return False
    for i in range(len(pub_tokens) - n + 1):
        if tuple(pub_tokens[i:i + n]) == kw_tokens:
            return True
    return False


async def _publisher_to_entity_map(
    db: AsyncSession, publisher_strs: set[str]
) -> dict[str, int]:
    """对每个 publisher 字符串，返回它命中的 entity_id；没命中则不在 dict 里。

    用 publisher_aliases 全表 + token 子序列匹配（与 routers/publishers 同口径），
    避免漏算同一公司的不同法人/简写。一个字符串理论上只该命中一个 entity（alias
    建档纪律），多命中时取首个。"""
    if not publisher_strs:
        return {}
    res = await db.execute(select(PublisherAlias.entity_id, PublisherAlias.keyword))
    # (entity_id, keyword token 串, keyword squash 键)——squash 用于连写/法人后缀回退。
    alias_list = [(eid, tuple(kt), corp_squash(kt)) for eid, kt in ((e, _toks(k)) for e, k in res.all())]
    alias_list = [(eid, kt, sq) for eid, kt, sq in alias_list if kt]
    out: dict[str, int] = {}
    for pub in publisher_strs:
        pub_toks = _toks(pub)
        if not pub_toks:
            continue
        pub_sq = corp_squash(pub_toks)
        for eid, kt, sq in alias_list:
            # 子序列命中 或 去后缀拼接后整段等值（修 "Topgames.Inc"↔"top games" 连写）。
            if _kw_hit(pub_toks, kt) or (sq and pub_sq == sq):
                out[pub] = eid
                break
    return out


def _pub_key(pub: str, pub_to_eid: dict[str, int]) -> str:
    """publisher 的「规范化身份键」：命中 entity 时用 \"@e:{eid}\"（覆盖跨法人/简写同公司），
    未命中时退回 normalize_ident(pub)（保住未建档主体的保守等同语义）。"""
    eid = pub_to_eid.get(pub)
    if eid is not None:
        return f"@e:{eid}"
    n = normalize_ident(pub)
    return n if n else ""


def _is_sibling(target_name_n: str, candidate_name_n: str) -> bool:
    """前缀匹配 + 最短 ≥ 5 字符。两侧规范化后比较。"""
    short, long = (target_name_n, candidate_name_n) if len(target_name_n) <= len(candidate_name_n) else (candidate_name_n, target_name_n)
    if len(short) < 5:
        return False
    return long.startswith(short)


async def find_sibling_app_ids(db: AsyncSession, target_app_id: str) -> list[str]:
    """返回与 target 同款的全部 app_id（含 target 自己）。

    若 target 没在 game_rankings 出现（无名字/publisher 锚），原样返回 [target]。
    """
    # 拉所有有名字的行，按 app_id 选一条"代表 name/publisher"。US 优先。
    res = await db.execute(
        select(
            GameRanking.app_id,
            GameRanking.country,
            GameRanking.name,
            GameRanking.publisher,
        ).where(GameRanking.name.is_not(None))
    )
    canonical: dict[str, tuple[str, str]] = {}  # app_id -> (name, publisher)
    for app_id, country, name, publisher in res.all():
        existing = canonical.get(app_id)
        if existing is None or country == "US":
            canonical[app_id] = (name, publisher or "")

    target = canonical.get(target_app_id)
    if not target:
        return [target_app_id]
    target_name, target_pub = target
    tname = normalize_ident(target_name)
    if not target_pub:
        # 没 publisher 锚——不敢跨匹配，保守只返自己。
        return [target_app_id]

    # 一次查全表 alias，建 publisher_str → entity_id 映射（去重后传入避免重复 token 化）。
    pub_strs = {pub for _, pub in canonical.values() if pub}
    pub_to_eid = await _publisher_to_entity_map(db, pub_strs)
    tkey = _pub_key(target_pub, pub_to_eid)
    if not tkey:
        return [target_app_id]

    result: list[str] = []
    for app_id, (name, publisher) in canonical.items():
        if app_id == target_app_id:
            result.append(app_id)
            continue
        if _pub_key(publisher, pub_to_eid) != tkey:
            continue
        if not _is_sibling(tname, normalize_ident(name)):
            continue
        result.append(app_id)
    return result
