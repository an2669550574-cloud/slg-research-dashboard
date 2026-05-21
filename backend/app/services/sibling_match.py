"""跨平台同款游戏识别：iOS 与 Android 的 app_id 不同，但同款游戏在
`game_rankings` 里会以两套 app_id 各自累积数据。详情页/聚合视图想以"游戏"
为单位展示时，需要把这些 app_id 视为同一族。

与前端 `lib/aggregateMerge.ts` **同款规则**——同 publisher（规范化等同）+
名字一方是另一方的规范化前缀且短的 ≥ 5 字符 → 同款。规则源于经验：
publisher 在 iOS/Play 之间常只差大小写/标点（"Century Games Pte. Ltd." vs
"Century Games PTE. LTD."）；名字一致或有"Game/Plus"之类后缀差异；超短公共
前缀容易把"Z" 误合"ZGame"，故设 5 字符门槛。

名字归一时优先取 US 行（country='US'）的，因为它通常是开发商提交的英文原名；
KR/JP 行往往是本地化（"킹샷:Kingshot"/"ホワイトアウト・サバイバル"），规范化
后跟其它平台的英文版不前缀匹配。
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GameRanking

_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_ident(s: Optional[str]) -> str:
    """去大小写、删非字母数字。'Last War:Survival Game' → 'lastwarsurvivalgame'。"""
    return _NORM_RE.sub("", (s or "").lower())


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
    tpub = normalize_ident(target_pub)
    tname = normalize_ident(target_name)
    if not tpub:
        # 没 publisher 锚——不敢跨匹配，保守只返自己。
        return [target_app_id]

    result: list[str] = []
    for app_id, (name, publisher) in canonical.items():
        if app_id == target_app_id:
            result.append(app_id)
            continue
        if normalize_ident(publisher) != tpub:
            continue
        if not _is_sibling(tname, normalize_ident(name)):
            continue
        result.append(app_id)
    return result
