# ADR 0005：RSS 早鸟信号层——次市场新品的零 ST 日级补偿

- 状态：已采纳（2026-07-09）
- 关联：ADR 0001（chart_type 维度）、P1-1 radar 影子行范式、docs/ARCHITECTURE.md 配额体系

## 背景 / 问题

ST 快照对次市场（JP/KR/DE/RU）双周一拍（错峰 #210 只摊开日期、不改每国频率——
配额宪法），新品上榜到被检出**平均滞后 ~7 天、最坏 14 天**，恰好错过软启动/首发窗
（买量调研最值钱的观察窗口）。加密 ST 同步被禁止；需要零配额的日级补偿信号源。

## 探针结论（2026-07-09）

Apple **旧版分类维度 RSS**（`itunes.apple.com/{cc}/rss/topgrossingapplications/
limit=N/genre=7017/json`）：
- 仍在服务（200），且 genre 参数**真实生效**（7017 策略 vs 6014 全游戏 vs 7012 桌游
  三组返回完全不同；JP 策略榜前排 = WoS/Last War，SLG 信号密度高）
- `feed.updated` 时间戳数小时前 = **日更级新鲜度**；封顶 100 名（请求 200 也只回 100）
- 免费、无鉴权、零 ST。新版 marketingtools RSS 无 genre 维度、无 top-grossing（404），
  旧版是唯一通路——**它是弃用状态的遗留服务，随时可能消失**，功能必须可静默降级。

## 决策

1. **绝不写 game_rankings**：RSS 榜与 ST 榜不同源（无收入/下载估算、深度不同），
   混写会污染 baseline / movement / 走势的快照语义。独立台账 `rss_chart_seen`。
2. 首轮整榜收编为基线不报（`is_baseline`，与 itunes_releases 同哲学）；之后每日
   diff，新面孔过三道闸：ST 已见（该国 iOS game_rankings 全史）/ 检出已见
   （market_newcomer_log）/ 忽略名单。
3. 真早鸟写 market_newcomer_log **影子行**（`chart_type='rss'`，radar 同款范式）：
   免费富化即时做，riding 既有中文化 / 子品类 / 视频管道；`/history` 排除、
   不进市场卡片网格。
4. 分发：**仅维护者卡**「⚡ RSS 早鸟」段（未过 ST 口径核实，对领导是噪声风险；
   ST 双周快照到位后同一 app 经正常检出通道进两卡，届时影子行已让它带上翻译/视频）。
   misfire 补跑台账已见 → 段不重复推。
5. 触发点：send_daily_digest 前置块（与 version_tracker 同位），不加独立 scheduler
   job；单国失败只降级该国，异常不拖垮 digest。
6. 范围：`RSS_EARLYBIRD_COUNTRIES`（默认 jp,kr——有 ST 双周口径可对照的次市场；
   DE/RU 榜单深度价值低暂不开）。空串 = 一键关闭。

## 取舍

- **不计入平淡日阈值**（`_primary_item_count`）：早鸟未核实，不该影响
  「平淡日兜底填充」的触发判定——宁可两段并存。
- 与 ST 检出的跨通道重复是**接受的**：RSS 早鸟先报（维护者卡）、ST 到位后正常检出
  再报（两卡）——后者是确认信号而非重复噪声；unique 约束按 chart_type 隔离天然并存。
- Android 无任何官方榜 RSS——iOS-only（与 ADR 0003/0004 同款取舍）。

## 回滚

`RSS_EARLYBIRD_COUNTRIES=` 置空即停（台账/影子行保留无害）；纯代码回退亦可
（0041 是纯新增表）。旧版 RSS 若被 Apple 退役：fetch 全 404 → 单国降级路径静默，
功能自然停摆，无需动代码。
