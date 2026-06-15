"""
微信公众号文章搜索服务

通过 wechat-download-api 搜索订阅的行业公众号中与游戏相关的文章，
用于新品监测推送时附上行业分析背景。
"""

import asyncio
import logging
import re
import time
from typing import List, Optional

import httpx
from pydantic import BaseModel
from sqlalchemy import func, select

from app.config import settings

_logger = logging.getLogger(__name__)

# 搜索接口会在命中词外包 <em class="highlight">…</em> 高亮标签，落进钉钉链接文字会很丑——清掉。
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: Optional[str]) -> str:
    return _TAG_RE.sub("", s or "").strip()


# 起步种子：表空时灌入（见 seed_wechat_accounts_if_empty），也是 DB 不可用时的兜底。
# 上线后订阅号改在看板维护（wechat_accounts 表），不再改这里。
_SEED_ACCOUNTS = {
    "游戏葡萄": "MjM5OTc2ODUxMw==",
    "游戏陀螺": "MjM5Njc5MjgyMA==",
}


async def _enabled_accounts() -> dict:
    """启用中的订阅号 {name: fakeid}，从 DB 读；DB 空或出错回退种子，保证搜索不空转。"""
    try:
        from app.database import AsyncSessionLocal  # 延迟 import：避开测试 reload 的陈旧 engine 绑定
        from app.models.wechat import WechatAccount
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(WechatAccount).where(WechatAccount.enabled.is_(True))
            )).scalars().all()
        if rows:
            return {r.name: r.fakeid for r in rows}
    except Exception as e:
        _logger.warning("读订阅号失败，回退种子: %s", e)
    return dict(_SEED_ACCOUNTS)


async def seed_wechat_accounts_if_empty() -> None:
    """表空时灌入种子订阅号（与 mock games / publishers 同款的启动 seed）。"""
    from app.database import AsyncSessionLocal
    from app.models.wechat import WechatAccount
    async with AsyncSessionLocal() as db:
        n = (await db.execute(select(func.count(WechatAccount.id)))).scalar() or 0
        if n:
            return
        for name, fakeid in _SEED_ACCOUNTS.items():
            db.add(WechatAccount(name=name, fakeid=fakeid, enabled=True))
        await db.commit()
        _logger.info("seeded %d wechat accounts", len(_SEED_ACCOUNTS))


async def search_biz(query: str, limit: int = 8) -> list[dict]:
    """按名搜公众号 → 候选 [{fakeid, nickname, alias}]（wechat-api /searchbiz）。连不上返 []。"""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{settings.WECHAT_API_BASE}/api/public/searchbiz", params={"query": query})
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        _logger.warning("searchbiz 失败: %s", e)
        return []
    out = []
    if data.get("success"):
        for a in (data.get("data", {}).get("list") or [])[:limit]:
            fid = a.get("fakeid", "")
            if fid:
                out.append({"fakeid": fid, "nickname": _strip_html(a.get("nickname")),
                            "alias": a.get("alias") or None})
    return out


class WechatArticle(BaseModel):
    """微信公众号文章摘要"""
    title: str
    digest: Optional[str] = None
    link: str
    author: str  # 公众号名称
    cover: Optional[str] = None
    publish_time: Optional[int] = None


class WechatLoginStatus(BaseModel):
    """wechat-api 登录状态（/api/admin/status）。"""
    logged_in: bool
    is_expired: bool
    expire_time_ms: Optional[int] = None  # 毫秒时间戳
    nickname: Optional[str] = None


async def get_login_status() -> Optional[WechatLoginStatus]:
    """查 wechat-api 登录状态。服务连不上返回 None（与「已过期」区分，避免误报过期）。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.WECHAT_API_BASE}/api/admin/status")
            resp.raise_for_status()
            d = resp.json()
    except Exception as e:
        _logger.warning("wechat 登录状态查询失败: %s", e)
        return None
    return WechatLoginStatus(
        logged_in=bool(d.get("loggedIn")),
        is_expired=bool(d.get("isExpired")),
        expire_time_ms=d.get("expireTime") or None,
        nickname=d.get("nickname") or None,
    )


async def _search_account(
    client: httpx.AsyncClient, name: str, fakeid: str,
    keyword: str, cutoff_timestamp: int,
) -> List[WechatArticle]:
    """搜单个公众号；失败只记 warning 返回空（一个号挂不拖累其余）。"""
    try:
        resp = await client.get(
            f"{settings.WECHAT_API_BASE}/api/public/articles/search",
            params={"fakeid": fakeid, "query": keyword, "count": 10},
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        _logger.warning("搜索 %s 失败: HTTP %s", name, e.response.status_code)
        return []
    except Exception as e:
        _logger.warning("搜索 %s 失败: %s", name, e)
        return []

    out: List[WechatArticle] = []
    if data.get("success"):
        for a in data.get("data", {}).get("articles", []):
            create_time = a.get("create_time", 0)
            if create_time and create_time < cutoff_timestamp:
                continue  # 过滤过时文章
            out.append(WechatArticle(
                title=_strip_html(a.get("title")),
                digest=_strip_html(a.get("digest")),
                link=a.get("link", ""),
                author=name,  # 用公众号名称代替 author
                cover=a.get("cover", ""),
                publish_time=create_time,
            ))
    return out


async def search_articles(
    keyword: str,
    limit: int = 3,
    days: int = 180,
) -> List[WechatArticle]:
    """
    在订阅公众号中搜索关键词相关的文章。

    Args:
        keyword: 搜索关键词（游戏名或厂商名）
        limit: 最多返回文章数
        days: 只搜索最近 N 天的文章，避免过时内容

    Returns:
        按时间倒序的文章列表，最多 limit 篇
    """
    cutoff_timestamp = int(time.time() - days * 86400)
    accounts = await _enabled_accounts()
    if not accounts:
        return []

    async with httpx.AsyncClient(timeout=15.0) as client:
        groups = await asyncio.gather(*[
            _search_account(client, name, fakeid, keyword, cutoff_timestamp)
            for name, fakeid in accounts.items()
        ])
    results = [a for group in groups for a in group]

    # 去重（按 link）
    seen = set()
    unique = []
    for a in results:
        if a.link and a.link not in seen:
            seen.add(a.link)
            unique.append(a)

    # 按发布时间倒序排序，取前 limit 篇
    unique.sort(key=lambda x: x.publish_time or 0, reverse=True)
    return unique[:limit]


def _dedup_sort(articles: List[WechatArticle], limit: int) -> List[WechatArticle]:
    seen, out = set(), []
    for a in articles:
        if a.link and a.link not in seen:
            seen.add(a.link)
            out.append(a)
    out.sort(key=lambda x: x.publish_time or 0, reverse=True)
    return out[:limit]


async def _discover_and_search(
    keyword: str, limit: int = 6, days: int = 180, max_accounts: int = 3,
) -> List[WechatArticle]:
    """兜底：订阅号 0 命中时，按 keyword 用 searchbiz 临时发现相关号（排除已订阅号，
    取前 max_accounts）再搜文章。只临时用、不入库——发现的多是游戏官方/小号，情报价值
    低于行业号，故仅作 fallback。"""
    subscribed = set((await _enabled_accounts()).values())
    cands = [c for c in await search_biz(keyword, limit=max_accounts + len(subscribed))
             if c["fakeid"] not in subscribed][:max_accounts]
    if not cands:
        return []
    cutoff_timestamp = int(time.time() - days * 86400)
    async with httpx.AsyncClient(timeout=15.0) as client:
        groups = await asyncio.gather(*[
            _search_account(client, c["nickname"], c["fakeid"], keyword, cutoff_timestamp)
            for c in cands
        ])
    return _dedup_sort([a for group in groups for a in group], limit)


async def search_multi_keywords(
    keywords: List[str],
    limit: int = 3,
    days: int = 180,
) -> List[WechatArticle]:
    """
    用多个关键词搜索，去重后返回。

    适用于同时搜游戏名和厂商名，增加命中率。
    """
    all_results = []
    for kw in keywords:
        if not kw:
            continue
        # 先精：订阅号；某关键词 0 命中 → 再广：searchbiz 临时发现相关号补搜（不入库）。
        articles = await search_articles(keyword=kw, limit=limit * 2, days=days)
        if not articles:
            articles = await _discover_and_search(kw, limit=limit * 2, days=days)
        all_results.extend(articles)

    return _dedup_sort(all_results, limit)
