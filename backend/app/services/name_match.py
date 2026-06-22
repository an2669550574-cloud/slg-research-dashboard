"""厂商名归一匹配原语（跨 slg_publishers / routers.publishers / sibling_match 共用）。

为何独立成模块：alias↔publisher 的「连续 token 子序列」匹配在三处各有一份（历史
注释「保持同步」）。本模块把**抗连写 + 抗法人后缀**的 squash 归一收成单一真源，
免得三处各加一遍 squash 逻辑后慢慢漂移。

核心问题（PUBLISHERS.md backlog）：alias `top games`（token `["top","games"]`）
匹配不上把品牌名连写的真实发行商 `Topgames.Inc`（token `["topgames","inc"]`）——
子序列匹配要求 token 边界对齐，连写就错位了，无字典又拆不开 "topgames"。补一条
**squash 等值**回退：双方去掉纯法人后缀（Inc/Ltd/PTE/LLC…）后把剩余 token 拼成
无分隔串再比较，`topgames` == `topgames` 即命中。

只做**等值**不做子串：子串会让 `igg` 误命中 `Trigger Games`（squash 后
"triggergames" 含 "igg"），破坏既有 word-boundary 语义（test_slg_publishers 有专门
回归用例）。等值天然安全——要求整段「非后缀名」完全相等，子序列路径仍负责
「alias 是 publisher 真子串」（如 `funplus` ⊂ `FunPlus International AG`）那类。

corp_squash 同时被 publisher_ignores 复用作「忽略名单」的归一键，让
"Niantic, Inc." 与 "Niantic Inc" 折叠到同一条忽略记录。
"""
from __future__ import annotations

# 纯「法人形式」后缀——去掉不改变品牌识别。**刻意不含** games / group / studio /
# global / holdings / technology / network 等描述性词：去掉它们会把 "Top Games" 与
# "Top Studio" 错并到同一 squash。只收公认的公司法律形式后缀。
CORP_SUFFIXES: frozenset[str] = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation",
    "company", "pte", "gmbh", "plc", "pty", "srl", "bv", "nv", "sa", "ag", "oy",
    "ab", "aps", "kk", "kg", "ohg", "spa", "sl", "sas", "sarl", "sdn", "bhd",
})


def corp_squash(tokens: list[str]) -> str:
    """去掉纯法人后缀 token 后拼成无分隔串。`["topgames","inc"]` → `"topgames"`。

    全是后缀 / 空 → 返回 `""`，调用方据此跳过（避免两个空串互等而误命中）。
    入参是**已分词**的 token 列表（与各调用方自己的 `_toks`/`_tokens` 同口径，
    都是小写 + 按非字母数字切），本函数只管去后缀 + 拼接，不重复分词。
    """
    return "".join(t for t in tokens if t not in CORP_SUFFIXES)
