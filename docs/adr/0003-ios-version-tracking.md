# ADR 0003：tracked iOS 竞品版本变更追踪

- 状态:**Accepted（A 后端 + B digest + C 前端 全完成，2026-06-26；全量 478 passed）**
- 日期:2026-06-26
- 关联:领导对新品监测的需求 ②（自动整理 分地区上线时间 / 版本更新 / 排行显著变化）；memory `project_newcomer_boss_requirements`；ADR 0002（同属新品监测，YT 视频）

---

## 背景

领导需求 ② 三子项的落地现状：
1. **排行显著变化** — ✅ 已有（每日 digest movement：空降/窜升/暴跌/收入异动）。
2. **版本更新** — 本 ADR 解决。此前 `version` 只在新品检出时一次性富化进 `market_newcomer_log`，**无变化检测、无历史、Game 表无字段**。
3. **分地区上线时间** — 暂不做（见「未做」），单独评估。

用户拍板（2026-06-26）：**范围只做版本追踪、对象只盯 tracked games**。

## 决策

### iOS-only —— 这是最该让后来者困惑的取舍
**Android 拿不到版本号**：Google Play 商品页的 JSON-LD（`appstore.fetch_play_apps` / `newcomer_log._enrich_android` 解析的同一份）**没有 version 字段**，也没有稳定上架日。iTunes lookup 则同响应自带 `version` + `currentVersionReleaseDate`，且零 Sensor Tower 配额。
→ 版本追踪**只查 `platform='ios'` 的 tracked games**。安卓版本追踪在没有可靠免费源前不做（别去翻 APK / 第三方站，超出省配额边界）。

### 机制
- **当前值**：`Game` 表加 `version` / `version_date`（alembic 0030，可空），作为比对基准 + 详情页展示。
- **trackId 来源（关键坑）**：iTunes 批量 lookup 需 iOS 数字 trackId，但 **HK tracked games 多用 GP 包名作 app_id**（iTunes 用包名查不到 iOS，GP 包名 ≠ iOS bundleId）。故 `Game` 加 `ios_track_id`（alembic 0031）存**人工核对的精确 iOS trackId**；`version_tracker._track_id` 优先用它、否则 app_id 本身是数字时用之、都没有则**跳过不追踪**（诚实留白）。曾试 iTunes search by 游戏名兜底，prod 实测否决（见备选）。
- **检测**：`services/version_tracker.py::check_tracked_versions` —— 按 trackId 批量 iTunes lookup（复用扩展后的 `appstore.fetch_apps_bulk`，一次 100 个、零额外请求）重查所有 tracked iOS games 版本，与 `Game.version` 比对。
  - **首次（version=NULL）填基线、不算变更**（no_baseline，与新品检测同哲学，避免功能上线把所有 app 当「刚更新」刷屏）。
  - **变了**：写一条 `game_histories(event_type='version', source='appstore')` 变更事件（title=`版本更新 X → Y`，description=release_notes）+ 更新 `Game` 当前值 + 收集进返回列表。
- **变更历史**：复用 `GameHistory`（`event_type='version'` 本就预留，**零新表**），详情页时间线天然能展示。
- **触发**：**内联进每日 digest 流程开头**——`send_daily_digest` 一开始就调 `check_tracked_versions`，落库 + 拿结构化变更（name/old/new/date）直接拼 digest「版本更新」段。check 在 webhook 检查**之前**跑（无 webhook 也积累版本历史）。不建独立 job：一次 check 既落库又供 digest，无时序耦合、changes 结构完整（免去查 GameHistory 反解析 title）。零 ST、USE_MOCK_DATA / 无 iOS games 时 no-op。版本检测随 digest 每日一次。

### 纵切片
| 切片 | 内容 | 状态 |
|---|---|---|
| A 后端核心 | Game 加 version/version_date + 迁移 0030 + `fetch_apps_bulk` 带 version + `version_tracker.check_tracked_versions` | ✅ done（迁移可逆） |
| B digest 段 | `send_daily_digest` 内联 check + `build_version_lines` 拼全局「版本更新」段 | ✅ done |
| C 前端 | GameDetail 时间线**已渲染** GameHistory version 事件（`EVENT_TYPE_CONFIG` fallback + i18n「版本更新」/「Update」），切片 A 落的事件直接可见 | ✅ 无需改 |

## 后果

**正面**：tracked iOS 竞品版本一更新，当天 digest 提醒 + 详情页时间线留痕，零 ST。
**负面 / 代价**：
- **Android 完全没有版本追踪**（GP 无版本源）——已知缺口，诚实留白。
- 带 schema 迁移（games 加列）：部署前打 `rollback-` tag。
- 仅追踪 tracked games（主动录入的）；未 track 的竞品不追——量可控、聚焦。

**回退**：job 空跑无害；`Game.version` 列留 NULL 无害；移除 job 即停。

## 未做（单独评估，非本 ADR 范围）
- **分地区上线时间对照**：iTunes 可多 storefront 查 `releaseDate`，但要 N 倍请求、且安卓全缺；价值待验证，不和版本追踪捆。
- 安卓版本源（无可靠免费途径）。

## 备选方案（已否决）
| 方案 | 否决原因 |
|---|---|
| 新建独立 version_snapshot 表 | `GameHistory` 已预留 `event_type='version'`，复用零新表；当前值进 Game 即够 |
| iTunes search by 游戏名兜底（非数字 app_id 时搜版本） | prod 实测同名歧义大 + 依赖 publisher 数据质量：`Warpath` 美区全是射击游戏、搜不到 Century 的 SLG Warpath（误匹配到 Lilith 的 `Warpath: Ace Shooter`）；`Lords Mobile`/`Vikings` 因 iOS 名带副标题漏。改走「人工补精确 `ios_track_id`」，零误匹配、缺的诚实留白 |
| 追踪所有检出 app（含 newcomer_log） | 用户拍板只盯 tracked；新品版本刚上线变化不大，且请求量大 |
| 独立 daily job（02:50 UTC）+ digest 读 GameHistory | 要查当天事件 + 从 title 反解析 old/new（脆弱）+ 处理「读哪天事件」（event_date≠检测日）；内联 check 直接拿结构化 changes，更简单，且 check 在 webhook 检查前跑、总落库。最初按独立 job 写、落码时改内联 |
