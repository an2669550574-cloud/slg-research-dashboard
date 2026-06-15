"""
微信公众号文章搜索服务

通过 wechat-download-api 搜索订阅的行业公众号中与游戏相关的文章，
用于新品监测推送时附上行业分析背景。
"""

import os
import logging
from typing import List, Optional
from datetime import datetime

import httpx
from pydantic import BaseModel

_logger = logging.getLogger(__name__)

# wechat-download-api 服务地址（本地默认）
WECHAT_API_BASE = os.getenv("WECHAT_API_BASE", "http://127.0.0.1:5001")

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
    results = []
    cutoff_timestamp = int((datetime.now().timestamp() - days * 86400))

    async with httpx.AsyncClient(timeout=15.0) as client:
        for name, fakeid in SUBSCRIBED_ACCOUNTS.items():
            try:
                resp = await client.get(
                    f"{WECHAT_API_BASE}/api/public/articles/search",
                    params={
                        "fakeid": fakeid,
                        "query": keyword,
                        "count": 10,  # 每个号取前10篇
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("success"):
                    for a in data.get("data", {}).get("articles", []):
                        # 过滤过时文章
                        create_time = a.get("create_time", 0)
                        if create_time and create_time < cutoff_timestamp:
                            continue

                        results.append(WechatArticle(
                            title=a.get("title", "").strip(),
                            digest=a.get("digest", "").strip(),
                            link=a.get("link", ""),
                            author=name,  # 用公众号名称代替 author
                            cover=a.get("cover", ""),
                            publish_time=create_time,
                        ))
            except httpx.HTTPStatusError as e:
                _logger.warning(f"搜索 {name} 失败: HTTP {e.response.status_code}")
            except Exception as e:
                _logger.warning(f"搜索 {name} 失败: {e}")

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
