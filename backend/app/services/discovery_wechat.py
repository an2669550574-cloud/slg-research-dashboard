"""发现层 · 公众号新品抽取扫描（期5a，只读）。

盯配置的出海新游公众号 → 拉最近文章 → LLM 从标题+摘要抽新品 {名/厂商/平台/品类/是否SLG} →
名→商店反解 app_id → 覆盖核查 → 出候选（**不落库**）。人工核候选后走 `/build-entity` 或 `/log`。

**零 ST**：只调 wechat-api（公众号）+ LLM 网关 + GP/iTunes 免费搜索。**探活门控**：session 挂 →
空返、不崩。**盘点体号**（游戏在正文里）从标题+摘要抽不出多少——全文抽取是后续增强（期5b）。
唯一自主够到「纯 GP 全新壳」长尾（Last Duo / Desire City 都由金角游戏这条刷出）的通路。
"""
import json
import re
import time

import httpx

from app.config import settings
from app.services import llm_gateway
from app.services.discovery_triage import _coverage, resolve_name_to_store
from app.services.wechat_articles import probe_articles_alive

# 出海新游发现源公众号（**fakeid 已钉**，避免「名→fakeid 反解」误命中——「王董的新游戏」按名
# 搜会串到「王董集团」建筑公司，见 2026-07-23 核查）。改源改这里 / 后续可迁 config。
_SOURCES = [
    {"name": "金角游戏", "fakeid": "MzU2ODg0NzI5NQ=="},      # 个案写稿，高产出（Last Duo/Desire City 源）
    {"name": "阿杜聊游戏", "fakeid": "MzYzOTA2MjA3NQ=="},    # 个案写稿，结构化
    {"name": "新游观察", "fakeid": "MzkyMzY2OTc5Mw=="},      # 盘点体，噪（游戏在正文，5a 产出少）
    {"name": "王董的新游戏", "fakeid": "Mzg5MTcwMTI5Nw=="},  # 盘点体，专盯测试/软启动 SLG
]

_EXTRACT_PROMPT = """你是手游竞品情报分析师。下面是一篇公众号文章的标题和摘要。抽取其中提到的\
**具体手游新品/新游**（尤其 SLG/策略/末日/国战/塔防/模拟经营类，或明确说新上线/软启动/测试的产品）。\
忽略：公众号自我介绍、纯行业评论、无具体游戏名的泛谈、非游戏内容。每个游戏输出对象：\
name(书名号内游戏名,中英文都要), publisher(厂商/发行商,没有则null), platform(ios/android/双端/未知), \
genre(原文品类描述), slg_relevant(bool,是否SLG/策略相关值得竞品调研)。严格输出 JSON 数组、无其它文字，\
没有可抽的游戏输出 []。

标题：{title}
摘要：{digest}"""


def _parse_arr(s: str) -> list:
    m = re.search(r"\[.*\]", s or "", re.S)
    if not m:
        return []
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else []
    except Exception:
        return []


async def _list_recent(fakeid: str, count: int) -> list[dict]:
    """拉某公众号最近 count 篇文章（/api/public/articles）。失败/session 挂返 []。"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{settings.WECHAT_API_BASE}/api/public/articles",
                                 params={"fakeid": fakeid, "count": count})
            d = r.json()
    except Exception:
        return []
    if not d.get("success"):
        return []
    return d.get("data", {}).get("articles") or []


async def extract_products(title: str, digest: str) -> list[dict]:
    """LLM 从标题+摘要抽新品候选。mock / 无 key → []（不瞎编）。"""
    if settings.USE_MOCK_DATA or not settings.TAISHI_API_KEY:
        return []
    try:
        resp = await llm_gateway.chat_completion(
            model=settings.TAISHI_TEXT_MODEL,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(
                title=title or "", digest=digest or "")}],
            max_tokens=1024, temperature=0.1)
        return _parse_arr(resp.choices[0].message.content if resp.choices else "")
    except Exception:
        return []


async def scan(days: int = 3, per_account: int = 5) -> dict:
    """扫所有源号最近 days 天的文 → 抽 SLG 新品 → 名→商店反解 → 覆盖核查 → 候选（只读）。"""
    alive = await probe_articles_alive()
    if alive is not True:
        return {"alive": False,
                "reason": "wechat session 探活失败（需扫码续期）" if alive is False else "wechat 服务连不上",
                "candidates": [], "stats": {}}
    cutoff = time.time() - days * 86400
    stats = {"articles": 0, "extracted": 0, "slg": 0, "resolved": 0, "unknown_slg": 0}
    candidates: list[dict] = []
    for src in _SOURCES:
        for a in await _list_recent(src["fakeid"], per_account):
            ct = a.get("create_time") or 0
            if ct and ct < cutoff:
                continue
            stats["articles"] += 1
            title, digest = a.get("title") or "", a.get("digest") or ""
            for p in await extract_products(title, digest):
                stats["extracted"] += 1
                if not p.get("slg_relevant"):
                    continue
                stats["slg"] += 1
                name = p.get("name")
                resolved = await resolve_name_to_store(name, p.get("platform")) if name else None
                coverage = "unresolved"
                if resolved:
                    stats["resolved"] += 1
                    coverage = await _coverage(resolved["app_id"])
                    if coverage == "unknown":
                        stats["unknown_slg"] += 1
                candidates.append({
                    "source": src["name"], "article_title": title[:80], "article_link": a.get("link"),
                    "name": name, "publisher": p.get("publisher"), "platform_hint": p.get("platform"),
                    "genre": p.get("genre"), "resolved": resolved, "coverage": coverage,
                })
    # unknown 的排前面（最值得人工建档）
    candidates.sort(key=lambda c: (c["coverage"] != "unknown", c["source"]))
    return {"alive": True, "days": days, "stats": stats, "candidates": candidates}
