# ADR 0001：榜单增加 `chart_type` 维度,并行采集下载/免费榜用于新品监测

- 状态:**Accepted（计划中,分切片实施）**
- 日期:2026-06-25
- 关联:`docs/ARCHITECTURE.md`「新品监测 + 每日情报 digest」「应用商店雷达」;Sensor Tower 配额体系

> ADR = Architecture Decision Record。只记录**难回滚、易让后来者困惑、且有真实取舍**的架构决策(本项目首份)。格式:状态 / 背景 / 决策 / 后果 / 备选方案。

---

## 背景

新品监测的榜单层目前**只采收入榜**(iOS `topgrossingapplications` / Android `topgrossing`),暴露两个结构性盲区:

1. **收入榜对新品不友好**:新品收入低、排名靠后甚至进不去。实测 Century Games《Top General》2026-06-19 首次进 US iOS 收入榜就在 rank 144,慢爬到峰值 90,**始终没进检测口径**(日志 Top100 / 日报 Top50),靠已建档主体宽口径(Top200)才勉强推送一次。
2. **iOS 收入榜深度有平台天花板 200**(Apple 品类榜上限,实测 5 个 iOS combo 都正好 200 行;Android 品类榜约 600)。加深榜单无解——200 就是苹果给的全部。

下载/免费榜按**装机速度**排序,装机量早于收入,是抓新品的更优早期信号。新 SLG(尤其中国出海)常在 **JP/KR** 先软启动、先在免费榜冒头,之后才进美区收入榜——只盯 US 收入榜恰是盲区。

**配额不是约束**:公司池 3000/月,实际用量 2026-05 = 103、2026-06(到 24 日)= 75,仅约 3%。

互补层「应用商店雷达」(开发者清单 diff)虽 rank 无关,但**只覆盖已建档厂商**;未建档新厂的下载榜放量它看不到,仍需榜单层补。

## 决策

### 1. 给 `game_rankings` 增加榜类型维度
- 新增列 `chart_type`(`'grossing'` | `'free'`),默认 `'grossing'`。
- 唯一约束 `uq_game_rankings_day_market` 由 `(app_id, date, country, platform)` 扩为**五元组**(加 `chart_type`)。SQLite 经 alembic `batch_alter_table` 重建表实现。
- 存量行回填 `'grossing'`。

### 2. 并行采集下载/免费榜,范围 US+JP+KR
- **两端都用「总免费榜」**:iOS `topfreeapplications` / Android `topselling_free`。不用 Android 的「新品免费榜」`topselling_new_free`——保持两端口径一致,**新品由我们自己的 first-appearance 逻辑筛**,不依赖平台的"新"定义。
- 范围 **US/JP/KR × 双端 = 6 combo**;US 日级、JP/KR 周级,沿用现有 cadence 门控,不新增节奏旋钮。
- 免费榜 `with_sales=False`(不取销量),1 次拉榜/combo/同步日。
- 配置:`RANKING_CHART_TYPE_IOS_FREE` / `RANKING_CHART_TYPE_ANDROID_FREE` / `FREE_CHART_COMBOS`(空 = 全关,一键回退)。

### 3. 检测按 `chart_type` 各自算 baseline
`_first_appearances` / `detect_newcomers` / `detect_publisher_newcomers` 加 `chart_type` 参数,**baseline 按榜类型隔离**——否则收入榜基线会吞掉免费榜首发。免费榜首发也进 `market_newcomer_log`(该表同步加 `chart_type` 列)。

### 4. 读路径全部钉死 `grossing`
详情/对比页 rank 趋势、今日榜视图、movement 异动、回填、sibling 匹配等**所有现有读查询显式过滤 `chart_type='grossing'`**,保证现有功能零回归。两榜数据不得混入同一趋势/检测。

### 5. digest 钉钉推送:下载榜新品**只推 is_slg=True**
- 收入榜维持现状:**故意不按 is_slg 过滤**(白名单滞后,按它过滤会误杀真新厂),仅用 `publisher_ignores` 剔人工确认噪声。
- **下载榜不同**:免费榜噪声更大(休闲/工具类装机榜混入多),钉钉推送**仅推 `is_slg=True`** 的下载榜新品,避免刷屏。
- **口径差异是刻意的**:非 SLG 的下载榜新品**仍照常入库 + 看板可见**,只是不进钉钉卡片。看板是全量沉淀,钉钉是高信噪比提醒。

### 6. 分纵切片实施
1. ✅ 地基(alembic 0026):迁移 + 采集入库 + 读路径钉死 grossing(零回归)。
2. ✅ 检测(alembic 0027):免费榜按 chart_type 各自 baseline 进检测/日志/digest;
   `build_free_newcomer_lines` 按 is_slg 门控钉钉;`/history?chart=` 筛榜类型(默认 grossing)。
3. 前端:新品页/详情页加「收入榜 / 下载榜」筛选与标识(待做)。

## 后果

**正面**
- 抓得到收入榜看不见的早期新品(装机放量先于收入);覆盖 JP/KR 亚洲软启动战场。
- `chart_type` 维度为将来扩别的榜(付费榜等)留好结构。

**负面 / 代价**
- 配额 +78/月 → ~153/月(占池 ~5%,可接受)。
- **带不可逆 schema 迁移**:部署前须按 `docs/ROLLBACK.md` 打 `rollback-` tag;回退走带迁移路径。
- 读路径漏钉一处 `grossing` 过滤 = 两榜混入趋势/检测(最大风险)。对策:过滤集中、每个读路径加回归测试。
- **is_slg 门控的取舍**:`is_slg` 白名单滞后维护,可能让个别"真新 SLG 厂"的下载榜新品**当时没进钉钉**(但仍入库+看板可见,事后建档即可在看板补归属)。这是为压下载榜噪声接受的代价。

**回退**:`FREE_CHART_COMBOS=""` 即停采集(纯配置,立即生效);`chart_type` 列与存量 free 行保留无害。

## 备选方案(已否决)

| 方案 | 否决原因 |
|---|---|
| 加深收入榜抓取(>200) | iOS 平台天花板 200,深不下去;Android 已 ~600。无效 |
| 只靠应用商店雷达 | 只覆盖已建档厂商;未建档新厂的下载榜放量看不到 |
| 下载榜只加 US | 漏掉 JP/KR 亚洲软启动(下载榜价值正在于早期信号),且配额不缺,无理由收窄 |
| Android 用「新品免费榜」`topselling_new_free` | 与 iOS 口径不对称;新品判定交给平台不如自己的 first-appearance 可控 |
