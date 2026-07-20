"""游戏名中文名解析——**以商店一手数据为准**，不采信 LLM 记忆。

起因（2026-07-20 抽样）：#250 让 LLM 顺带产出游戏中译名，4 条样本里 3 条命中真实官方名，
第 4 条 `Tribal Wars`(InnoGames) 被译成《部落战争》——这游戏**没有中文区发行**，中文圈一律
用英文原名，而《部落战争》恰好是 Clash of Clans 的知名民间别名。领导拿它去搜会搜到另一款游戏，
**比看英文原名更糟**。

失败模式很清楚：有中文区发行的，LLM 命中真名；没有的，它直接字面直译、且不认为自己"拿不准"，
所以 prompt 里那句「拿不准就留空」拦不住。改用客观校验：**查该 app 在中文区商店的实际标题**。

- iOS：iTunes lookup（免费，零 ST），按 cn → tw → hk 顺序取第一个**含汉字**的 trackName。
- Android：Google Play 页面 hl=zh-CN / zh-TW 的标题，同样要求含汉字。

「含汉字」这道判据是关键：中文区有上架 ≠ 标题做了本地化。实测 Tribal Wars 在台港有上架，
但标题栏就是英文 `Tribal Wars` → 判为无中文名 → 渲染层保留原名。正是要的行为。

查不到中文名返回 None，语义是「该游戏没有官方中文名」，**不是**「还没查」——调用方据此保留原名。
"""
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# 中文区优先级：国服官方名最通行，其次台、港。同一 IP 常有多个官方中文名
# （Guns of Glory 实测：cn=火器文明 / GP繁中=火器時代 / App Store台=迷失大陸）。
_IOS_STOREFRONTS = ("cn", "tw", "hk")
_GP_LOCALES = (("zh_CN", "cn"), ("zh_TW", "tw"))

_CJK_RE = re.compile(r"[一-鿿]")
_ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_GP_BASE = "https://play.google.com/store/apps/details"
_GP_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
# GP 页标题在 og:title / <title> 里，形如「游戏名 - Google Play 上的应用」
_GP_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.I)


def has_cjk(s: Optional[str]) -> bool:
    """标题是否真含汉字。中文区有上架 ≠ 标题本地化了（Tribal Wars 台港标题仍是英文）。"""
    return bool(s and _CJK_RE.search(s))


async def _ios_cn_name(track_id: str) -> Optional[str]:
    """iOS：按 cn → tw → hk 找第一个含汉字的官方标题。"""
    async with httpx.AsyncClient(timeout=10) as client:
        for cc in _IOS_STOREFRONTS:
            try:
                resp = await client.get(_ITUNES_LOOKUP, params={
                    "id": track_id, "country": cc, "entity": "software"})
                if resp.status_code != 200:
                    continue
                results = resp.json().get("results") or []
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("iTunes lookup %s/%s 失败: %s", track_id, cc, e)
                continue
            if not results:
                continue        # 该区未上架
            name = results[0].get("trackName")
            if has_cjk(name):
                return name.strip()
    return None


async def _android_cn_name(package: str) -> Optional[str]:
    """Android：抓 GP 页 zh-CN / zh-TW 的 og:title，取第一个含汉字的。"""
    async with httpx.AsyncClient(timeout=12, headers=_GP_HEADERS) as client:
        for hl, gl in _GP_LOCALES:
            try:
                resp = await client.get(_GP_BASE, params={"id": package, "hl": hl, "gl": gl},
                                        follow_redirects=True)
                if resp.status_code != 200:
                    continue
                m = _GP_TITLE_RE.search(resp.text)
            except httpx.HTTPError as e:
                logger.warning("GP 页 %s/%s 失败: %s", package, hl, e)
                continue
            if not m:
                continue
            # og:title 尾部常带「 - Google Play 上的应用」之类后缀，按分隔符截首段
            title = m.group(1).split(" - ")[0].split(" – ")[0].strip()
            if has_cjk(title):
                return title
    return None


async def fetch_store_cn_name(app_id: str, platform: Optional[str] = None) -> Optional[str]:
    """app_id → 中文区商店的官方中文标题；无则 None（= 该游戏没有官方中文名，保留原名）。

    platform 省略时按 app_id 形态推断：纯数字 = iOS track id，含 `.` = Android 包名
    （与 _store_url 同款判据）。零 ST 配额，两侧都是公开免费接口。
    """
    aid = (app_id or "").strip()
    if not aid:
        return None
    is_ios = aid.isdigit() if platform is None else platform == "ios"
    try:
        return await (_ios_cn_name(aid) if is_ios else _android_cn_name(aid))
    except Exception:
        logger.warning("商店中文名解析异常 %s", aid, exc_info=True)
        return None
