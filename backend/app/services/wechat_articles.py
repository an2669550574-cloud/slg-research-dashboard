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

from app.config import settings

_logger = logging.getLogger(__name__)

# 搜索接口会在命中词外包 <em class="highlight">…</em> 高亮标签，落进钉钉链接文字会很丑——清掉。
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: Optional[str]) -> str:
    return _TAG_RE.sub("", s or "").strip()

# 订阅的行业公众号（需要登录 wechat-download-api 后手动获取 fakeid）
# 当前仅配置游戏葡萄和游戏陀螺作为示例
SUBSCRIBED_ACCOUNTS = {
    "游戏葡萄": "MjM5OTc2ODUxMw==",
    "游戏陀螺": "MjM5Njc5MjgyMA==",
    # 更多公众号可以后续添加，如：
    # "手游那点事": "xxx",
    # "竞核": "xxx",
}


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

    async with httpx.AsyncClient(timeout=15.0) as client:
        groups = await asyncio.gather(*[
            _search_account(client, name, fakeid, keyword, cutoff_timestamp)
            for name, fakeid in SUBSCRIBED_ACCOUNTS.items()
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
        articles = await search_articles(keyword=kw, limit=limit * 2, days=days)
        all_results.extend(articles)

    # 去重
    seen = set()
    unique = []
    for a in all_results:
        if a.link and a.link not in seen:
            seen.add(a.link)
            unique.append(a)

    # 按时间排序
    unique.sort(key=lambda x: x.publish_time or 0, reverse=True)
    return unique[:limit]
