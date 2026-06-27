# ADR 0004：tracked iOS 竞品分地区上线时间对照

- 状态:**Accepted（后端 + 前端全完成，2026-06-27；全量 485 passed）**
- 日期:2026-06-27
- 关联:领导对新品监测的需求 ②「自动整理 分地区上线时间 / 版本更新 / 排行变化」**子项③**；ADR 0003（需求② 版本追踪，已上线）把分地区上线列为「未做、单独评估」，本 ADR 即那次评估的落地。

---

## 背景

需求 ② 三子项里，①排行变化（movement）+ ②版本更新（ADR 0003）已上线，**③分地区上线时间**此前标「单独评估」。本次评估（2026-06-27，iTunes 实测）结论：

- **数据拿得到**：iTunes lookup 的 `releaseDate` 随 `country` 参数**分地区不同**（实测 Whiteout Survival 德区 2022-12-02 早于美区 2023-02-12 = soft-launch 区序），零 Sensor Tower 配额。
- **成本廉价**：按 country 循环、每轮一次批量 lookup 含全部 trackId，**请求数 = storefront 数（与游戏数无关）**；~10 storefront = 10 次免费请求/轮。ADR 0003 当初顾虑的「N 倍请求」不是 ST 配额问题（iTunes 免费）。
- **价值**：看清竞品「在哪些区先测先上、扩区节奏」，是出海买量/上线节奏的情报。

## 决策

### iOS-only（沿用 ADR 0003 的取舍）
Google Play 商品页无可靠上架日（JSON-LD 无 release date）→ 只查 `platform='ios'` 的 tracked games。Android 分地区上线在没有可靠免费源前不做。

### 机制
- **trackId 来源**：复用 `version_tracker._track_id`（`ios_track_id` 优先、否则数字 app_id、GP 包名无 trackId 则跳过）。与版本追踪同一份人工核对的精确 trackId。
- **数据模型**：新表 `game_region_release(app_id, country, release_date, checked_at)`，`(app_id, country)` 唯一。**专用表而非塞进 GameHistory**：上架日是「参考数据」（多为数年前、近静态），价值在**跨区对照排序**而非时间线事件；塞 timeline 会用大量同日老事件刷屏、也表达不出「区序」。
- **采集**：`services/region_launch.py::sync_region_launches` 按 `REGION_LAUNCH_STOREFRONTS`（默认 us,jp,kr,tw,cn,de,gb,fr,ca,br）逐 country 批量 lookup → upsert `release_date`。`fetch_apps_bulk` 扩展返回 `release_date`（随 country 不同；version_tracker 不读此键，无副作用）。
- **该区查不到（resultCount=0）**：也落一行记 `release_date=NULL`——与「该区是另一个 trackId」区分不开，**诚实留空**，不臆测「未上线」。
- **触发**：独立**周级** job（每周一 04:20 UTC，视频 03:50 + DB 备份 04:00 之后）。上架日近静态，周级足够；refresh 顺带捕捉竞品「新进某区」。另有 `POST /api/games/regions/sync` 手动刷（供首次填充 / 即时更新）。零 ST，`USE_MOCK_DATA` / 无可用 trackId 时 no-op。
- **展示**：`GET /api/games/{app_id}/regions` 按上架日升序（最早区先 = soft-launch 区序，NULL 沉底）；前端 GameDetail 新增「分地区上线」区块——国旗 + 国家码 + 上架日，**最早区高亮**，无数据整段不渲染。

### 纵切片
| 切片 | 内容 | 状态 |
|---|---|---|
| 后端 | 表 + 迁移 0033 + `fetch_apps_bulk` 带 release_date + `region_launch.sync_region_launches` + 周级 job + `GET /{app_id}/regions` + `POST /regions/sync` + 4 测试 | ✅ done（迁移可逆） |
| 前端 | GameDetail `RegionLaunch` 区块 + i18n（分地区上线 / Regional Launch）+ `gamesApi.regions` | ✅ done（preview 实测：DE 高亮最早 / 升序 / NULL=未上架查无 / build 过） |

## 后果

**正面**：tracked iOS 竞品分地区上架日一目了然，看清扩区节奏 / soft-launch 先行区，零 ST。
**负面 / 代价**：
- **Android 无分地区上线**（GP 无上架日源）——已知缺口，诚实留白。
- `resultCount=0` 区分不了「该区未上架」vs「该区是另一个 trackId」→ 统一记 NULL（前端显示「未上架 / 查无」），不臆测。
- 带 schema 迁移（新增表）：部署前打 `rollback-` tag。
- 仅 tracked games（主动录入的）；未 track 的竞品不查。

**回退**：job 空跑无害；表留空无害；移除 job + 前端区块即停。迁移纯加表，回滚走纯代码（旧码无此表无副作用）。

## 备选方案（已否决）
| 方案 | 否决原因 |
|---|---|
| 上架日写进 `GameHistory`（event_type='region_launch'） | 上架日是近静态参考数据、价值在跨区对照排序而非时间线；塞 timeline 会被大量同日老事件刷屏、且表达不出区序。专用表 + 排序端点更贴价值 |
| 多 storefront 查**版本**而非上架日 | 版本已由 ADR 0003 单区（us）追踪；分地区要的是「首次上架日」，releaseDate 正是 |
| `resultCount=0` 记为「未上线」 | 与「该区另一个 trackId」区分不开，会误报未上线；统一 NULL 诚实留空 |
| 日级 job | 上架日近静态，日级是浪费；周级足够，手动 `POST /regions/sync` 兜即时需求 |
