/**
 * 后端 API 响应类型，对应 backend/app/schemas/*.py 的 Pydantic 模型。
 *
 * 维护方式：
 * - 现阶段手写。新增/修改 Pydantic schema 后**必须**同步改这里。
 * - 当切到 OpenAPI 自动生成时，把本文件替换为生成的 `types.gen.ts` 即可，
 *   API 表面不变。
 *
 * 命名约定：和 Pydantic 类名一一对应，方便对账。
 */

/** ISO 日期字符串，"YYYY-MM-DD" 或带时区的 datetime 字符串 */
type IsoDateString = string

// ─── games ───────────────────────────────────────────────────────────────

export interface GameOut {
  id: number
  app_id: string
  name: string
  publisher: string | null
  icon_url: string | null
  category: string
  platform: string
  country: string
  release_date: string | null
  description: string | null
  tags: string[]
  created_at: IsoDateString
  updated_at: IsoDateString
}

export interface GameCreate {
  app_id: string
  name?: string | null
  publisher?: string | null
  icon_url?: string | null
  platform?: string
  country?: string
  release_date?: string | null
  description?: string | null
  tags?: string[]
}

export type GameUpdate = Partial<Omit<GameCreate, 'app_id'>>

export interface RankingTodayOut {
  app_id: string
  name: string | null
  publisher: string | null
  icon_url: string | null
  rank: number | null
  downloads: number | null
  revenue: number | null
  date: string | null
  is_slg: boolean
}

export interface TrendPoint {
  date: string
  value: number | null
  rank: number | null
}

export interface MetricsOut {
  rankings: TrendPoint[]
  downloads: TrendPoint[]
  revenue: TrendPoint[]
}

/** GET /games/{app_id}/coverage：该 app 本地实际有数据的国家/平台组合，
 *  按销量覆盖最全→最少排，[0] 即详情页最佳默认。 */
export interface MetricsCoverage {
  country: string
  platform: string
  days: number
  sales_days: number
  rank_days: number
}

/** GET /games/aggregate-leaderboard：跨该 app 全部已监测市场在窗口内合计
 *  下载/收入，与详情页头部「已监测市场合计」同口径，可直接对账。 */
export interface AggregateLeaderboardOut {
  app_id: string
  name: string | null
  publisher: string | null
  icon_url: string | null
  downloads: number
  revenue: number
}

// ─── history ─────────────────────────────────────────────────────────────

export interface HistoryOut {
  id: number
  app_id: string
  event_date: string
  event_type: string
  title: string
  description: string | null
  source: string
  created_at: IsoDateString
}

export interface HistoryCreate {
  app_id: string
  event_date: string
  event_type: string
  title: string
  description?: string | null
  source?: string
}

// ─── materials ───────────────────────────────────────────────────────────

export type MaterialAnalysisStatus = 'pending' | 'running' | 'done' | 'failed'

export interface MaterialScene {
  ts: number
  description: string
}

export interface MaterialHook {
  ts: number
  kind: string
  note: string
}

export interface MaterialOut {
  id: number
  app_id: string
  title: string
  url: string | null
  source: 'link' | 'upload'
  file_name: string | null
  file_size: number | null
  mime_type: string | null
  stream_url: string | null
  platform: string | null
  material_type: string
  tags: string[]
  notes: string | null
  created_at: IsoDateString
  // LLM 视频分析（null/undefined 视同尚未分析）
  analysis_status: MaterialAnalysisStatus | null
  analysis_brief: string | null
  analysis_tags: string[] | null
  analysis_scenes: MaterialScene[] | null
  analysis_hooks: MaterialHook[] | null
  analyzed_at: IsoDateString | null
  analysis_model: string | null
  analysis_cost_usd: number | null
  analysis_error: string | null
  // 关键帧 + 联系单（migration 0007）：URL 含 HMAC 短时令牌
  analysis_frames: { ts: number; url: string }[] | null
  analysis_contact_sheet_url: string | null
  // 结构化标签（P2）：素材在各一级标签维度下已打的值
  tag_values: MaterialTagValueItem[]
}

// ─── 创意迁移（adapt）─────────────────────────────────────────────

export interface CreativeKeyHook {
  ts_est: string
  kind: string
  note: string
}

export interface CreativeDirection {
  name: string
  concept: string
  borrows_from_ref: string
  fit_to_self_product: string
  opening_3sec: string
  key_hooks: CreativeKeyHook[]
  ending_cta: string
  risk_notes: string
}

export interface CreativeConstraintsCheck {
  no_grand_opening: string
  no_cg_promo: string
  one_event_per_shot: string
  one_action_in_first_1_5s: string
  feedback_separate_shot: string
}

export interface CreativeDirectionsResult {
  data: { directions: CreativeDirection[]; constraints_check?: CreativeConstraintsCheck }
  cost_usd: number
  model: string
}

export interface CreativeShot {
  ts: string
  shot_type: string
  visual: string
  audio_voiceover: string
  production_notes: string
}

export interface CreativeScriptResult {
  data: {
    direction_name: string
    total_duration_sec: number
    shots: CreativeShot[]
    constraints_check?: CreativeConstraintsCheck
  }
  cost_usd: number
  model: string
}

// 阶段 1 返回新增 id（用于把后续脚本回写到同一条历史存档）
export interface CreativeDirectionsResponse extends CreativeDirectionsResult {
  id: number
}

// 一条创意迁移历史存档（方向 run + 可选脚本）
export interface CreativeAdaptationOut {
  id: number
  material_id: number
  our_product: string
  product_id: number | null
  data: { directions: CreativeDirection[]; constraints_check?: CreativeConstraintsCheck }
  model: string | null
  cost_usd: number | null
  chosen_index: number | null
  chosen_name: string | null
  script: CreativeScriptResult['data'] | null
  script_model: string | null
  script_cost_usd: number | null
  created_at: string | null
  script_updated_at: string | null
}

export interface MaterialCreate {
  app_id: string
  title: string
  url: string
  platform?: string | null
  material_type?: string
  tags?: string[]
  notes?: string | null
  tag_values?: MaterialTagValueInput[]
}

// app_id 可改：把已有素材重新归类到游戏（空串 = 取消关联）。
export type MaterialUpdate = Partial<MaterialCreate>

/** GET /materials/tags：标签 + 该标签下素材数，热度降序。 */
export interface MaterialTagCount {
  tag: string
  count: number
}

// ─── movements (今日大事) ────────────────────────────────────────────────

export type MovementKind = 'new_entrant' | 'surge' | 'drop' | 'revenue_spike'

export interface MovementEvent {
  kind: MovementKind
  country: string
  platform: string
  today: string
  prev_date: string
  app_id: string
  name: string
  icon_url: string | null
  prev_rank: number | null
  cur_rank: number | null
  prev_revenue: number | null
  cur_revenue: number | null
  revenue_pct: number | null
}

export interface MovementsOut {
  today: string
  events: MovementEvent[]
  combos_without_baseline: string[]
  /** ST 配额耗尽 / 同步失败导致今日 game_rankings 缺失或不完整的 combo;
   *  这些 combo 不参与异动对比(否则会满屏"跌出 TOP"),前端单独提示。 */
  combos_with_stale_today: string[]
}

// ─── newcomers（新品监测）──────────────────────────────────────────────

/** 一条"新面孔"：过去 W 个快照没出现过、as_of 进 TopN 的产品。零 ST 配额。 */
export interface NewcomerItem {
  country: string
  platform: string
  as_of: string
  app_id: string
  name: string
  publisher: string | null
  icon_url: string | null
  rank: number | null
  revenue: number | null
  downloads: number | null
  /** 发行商命中 SLG 白名单。仅用于前端区分"已识别 SLG"vs"新厂商待识别"。 */
  is_slg: boolean
}

/** 一条已沉淀的新面孔检出（检出即落库 + 免费源富化，未富化字段为 null）。 */
export interface NewcomerHistoryItem {
  id: number
  country: string
  platform: string
  app_id: string
  as_of: string
  name: string
  publisher: string | null
  icon_url: string | null
  rank: number | null
  revenue: number | null
  is_slg: boolean
  first_detected_at: IsoDateString
  store_url: string | null
  release_date: string | null
  genre: string | null
  rating: number | null
  rating_count: number | null
  price: string | null
  description: string | null
  screenshots: string[]
  enrich_source: 'itunes' | 'gp' | null
  /** 读时归属：命中已建档主体（建档后立即生效，无需等下轮检出）。 */
  entity_id: number | null
  entity_name: string | null
}

export interface NewcomerHistoryOut {
  today: string
  items: NewcomerHistoryItem[]
  days: number
}

export interface NewcomersOut {
  today: string
  items: NewcomerItem[]
  /** 缺历史快照(冷库/首次同步)、无从判断"新面孔"的 combo。 */
  combos_without_baseline: string[]
  /** 各 combo 锚定的"最近快照日"，前端显示"数据截至 X"。 */
  as_of_by_combo: Record<string, string>
  /** 当次生效的判定口径——回看窗口数 / 名次门槛。 */
  window: number
  topn: number
}

/** 已建档厂商主体的一条新品：首次出现在已监测榜单（任意名次，不设 TopN 门槛）。 */
export interface PublisherNewcomerItem {
  country: string
  platform: string
  as_of: string
  app_id: string
  name: string
  publisher: string | null
  icon_url: string | null
  rank: number | null
  revenue: number | null
  downloads: number | null
  entity_id: number
  entity_name: string
  /** 'alias' = 发行马甲命中 / 'app_id' = 钉选命中 */
  matched_by: 'alias' | 'app_id'
}

export interface PublisherNewcomersOut {
  today: string
  items: PublisherNewcomerItem[]
  combos_without_baseline: string[]
  as_of_by_combo: Record<string, string>
  window: number
}

/** 一条「App Store 新上架」：开发者账号清单 diff 出的新 app（不依赖进榜）。 */
export interface AppstoreReleaseItem {
  entity_id: number
  entity_name: string
  artist_label: string | null
  /** 'ios' = iTunes 清单 diff；'gp' = Google Play 开发者页（track_id=包名）。 */
  platform: 'ios' | 'gp'
  track_id: string
  name: string
  bundle_id: string | null
  release_date: string | null
  track_view_url: string | null
  /** 以下展示字段来自免费 iTunes lookup 同响应，零增量 ST 配额。 */
  artwork_url: string | null
  genre: string | null
  rating: number | null
  rating_count: number | null
  price: string | null
  /** 可见 storefront 小写码（us 不在列 = 疑似软启动）与检出详情。 */
  storefronts: string[]
  description: string | null
  screenshots: string[]
  first_seen_at: IsoDateString
}

export interface AppstoreReleasesOut {
  today: string
  items: AppstoreReleaseItem[]
  /** 已挂账号数 / 已完成首次基线同步的账号数。 */
  artists_total: number
  artists_synced: number
  days: number
}

// ─── quota ───────────────────────────────────────────────────────────────

export type DataSource = 'real_api' | 'mock' | 'snapshot_stale'

/** ST 公司账户级用量（/v1/api_usage 拉的，跨团队共享 3000/月）。
 *  null = mock 模式 / 无 API key / 从未成功拉过——前端隐藏该行。 */
export interface AccountOrgUsage {
  usage: number | null
  limit: number | null
  /** limit - usage，公司池剩余次数；驱动全局警示条阈值判定 */
  remaining: number | null
  percentage: number
  tier: string | null
}

/** 公司账户池状态（后端 quota._classify_state 给出）：
 * - normal: 充裕，无需提示
 * - low: 剩余 ≤ SENSOR_TOWER_ORG_LOW_THRESHOLD，弹黄条提醒
 * - reserved: 剩余 ≤ SENSOR_TOWER_ORG_RESERVE，本项目已主动停拉，弹红条
 */
export type AccountState = 'normal' | 'low' | 'reserved'

/** GET /api/quota/history：近 N 天本项目每日调用次数,缺失天填 0。
 *  仅本项目计数(api_quota_daily),不含公司池;前向记录(daily 表上线前的日子全 0)。 */
export interface QuotaHistoryPoint {
  date: string  // "YYYY-MM-DD"
  count: number
}

export interface QuotaHistoryOut {
  days: number
  points: QuotaHistoryPoint[]
}

export interface QuotaInfo {
  year_month: string
  used: number
  limit: number
  remaining: number
  percentage: number
  exhausted: boolean
  data_source?: DataSource
  data_updated_at?: string | null
  /** 公司账户级用量；null 表示无法获取（mock/无 key/拉取失败且无历史快照） */
  organization?: AccountOrgUsage | null
  /** 当前 token 持有者贡献的次数（ST 服务端口径，与本地 used 不同源） */
  account_user_usage?: number | null
  /** account_usage 是否走的是过期回退 */
  account_stale?: boolean | null
  /** 公司池状态：驱动全局警示条 + 本项目是否自停拉取 */
  account_state?: AccountState
}

// ─── iTunes lookup（不是 Pydantic，但前端用到）──────────────────────────────

export interface AppLookupResult {
  name?: string | null
  publisher?: string | null
  icon_url?: string | null
  release_date?: string | null
  description?: string | null
  version?: string | null
  release_notes?: string
  genres?: string[]
}

// ─── 通用 ────────────────────────────────────────────────────────────────

/** 分页响应：后端把总数放在 X-Total-Count 响应头里。 */
export interface PagedResponse<T> {
  items: T[]
  total: number
}

export interface DeleteResponse {
  message: string
  app_id?: string
  id?: number
}

export interface OwnProduct {
  id: number
  name: string
  brief: string
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface OwnProductCreate {
  name: string
  brief: string
  is_default?: boolean
}

export type OwnProductUpdate = Partial<OwnProductCreate>

export interface OwnProductMaterial {
  id: number
  own_product_id: number
  asset_type: 'video' | 'image' | 'text'
  title: string | null
  file_name: string | null
  file_size: number | null
  mime_type: string | null
  text_content: string | null
  created_at: string
  preview_url: string | null
}

export interface OwnProductAnalyzeResult {
  brief: string
  theme: string | null
  gameplay: string | null
  selling_points: string[] | null
  audience: string | null
  differentiation: string | null
  cost_usd: number
  model: string
  material_count: number
}

// ─── 标签库（tag taxonomy）────────────────────────────────────────────────

export type TagValueType = 'text' | 'date'

/** 二级标签（受控值）。仅「文字」型一级标签下挂二级；「时间」型打标签时选日期。 */
export interface TagOption {
  id: number
  dimension_id: number
  value: string
  sort_order: number
  created_at: IsoDateString
}

/** 一级标签（维度 / 框架）。value_type=date 时 options 恒空。 */
export interface TagDimension {
  id: number
  name: string
  value_type: TagValueType
  material_type: string | null
  is_required: boolean
  allow_multi: boolean
  sort_order: number
  created_at: IsoDateString
  options: TagOption[]
}

export interface TagDimensionCreate {
  name: string
  value_type?: TagValueType
  material_type?: string | null
  is_required?: boolean
  allow_multi?: boolean
  sort_order?: number
}

// value_type 不可改（后端刻意省略）：text↔date 切换会让既有数据语义错乱。
export type TagDimensionUpdate = Partial<Omit<TagDimensionCreate, 'value_type'>>

export interface TagOptionCreate {
  value: string
  sort_order?: number
}

export type TagOptionUpdate = Partial<TagOptionCreate>

// ─── 结构化打标签（P2）──────────────────────────────────────────────────

/** 素材上一条已打标记（含维度元信息，免前端再 join）。 */
export interface MaterialTagValueItem {
  dimension_id: number
  dimension_name: string
  value_type: TagValueType
  option_id: number | null
  value: string | null
  value_date: string | null
}

/** 打标签提交：一个维度一条。text 给 option_ids，date 给 value_date。 */
export interface MaterialTagValueInput {
  dimension_id: number
  option_ids?: number[]
  value_date?: string | null
}

// ─── 聚合分析（P4）─────────────────────────────────────────────────────
// 按某文字型一级标签统计去重素材分布；可选第二维度做交叉透视。零 ST 配额。

export interface TagAggregateSubBucket {
  option_id: number
  value: string
  count: number
}

export interface TagAggregateBucket {
  option_id: number
  value: string
  count: number
  /** 仅交叉透视时存在：该主桶下按第二维度的细分。 */
  sub?: TagAggregateSubBucket[] | null
}

export interface TagAggregateOut {
  dimension_id: number
  dimension_name: string
  by_dimension_id: number | null
  by_dimension_name: string | null
  total_materials: number
  tagged_materials: number
  buckets: TagAggregateBucket[]
}

export interface TagAggregateParams {
  dimension_id: number
  by?: number
  app_id?: string
  material_type?: string
  tag_options?: string
}

// ── AI 标签分析 Agent（P6）─────────────────────────────────────────
export type TagAnalysisMode = 'report' | 'chat'
export type TagAnalysisModel = 'claude-sonnet-4.5' | 'claude-opus-4.7'

export interface TagAnalysisMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  model?: string | null
  cost_usd?: number | null
  material_count?: number | null
  created_at: string
}

export interface TagAnalysisSession {
  id: number
  title: string
  app_id?: string | null
  material_type?: string | null
  tag_options?: string | null
  model: string
  created_at: string
  updated_at: string
  messages: TagAnalysisMessage[]
}

export interface TagAnalysisSessionListItem {
  id: number
  title: string
  material_type?: string | null
  model: string
  message_count: number
  created_at: string
  updated_at: string
}

/** 跑一轮分析：session_id 空=新建报告会话；带 session_id + mode=chat=追问。 */
export interface TagAnalysisRunRequest {
  session_id?: number
  mode: TagAnalysisMode
  message?: string
  model: TagAnalysisModel
  app_id?: string
  material_type?: string
  tag_options?: string
}

/** 单次报告分析的成本干跑预估（不打网关、零配额）。empty/over_limit 时不给金额。 */
export interface TagAnalysisEstimate {
  material_count: number
  limit: number
  empty: boolean
  over_limit: boolean
  model: string
  input_tokens_est: number
  output_tokens_est: number
  estimated_cost_usd: number
}

/** 删除一级 / 二级标签的返回：含连带清理的计数。 */
export interface TagDeleteResponse {
  message: string
  id: number
  removed_options?: number
  removed_material_tags?: number
}

// ─── 厂商主体（publisher entities）────────────────────────────────────────

export interface PublisherAlias {
  id: number
  keyword: string
  label: string | null
}

export interface PublisherAppId {
  id: number
  app_id: string
  note: string | null
}

/** 主体的应用商店开发者账号（ios=iTunes artistId / gp=GP 开发者页 id）。清单 diff 抓"未进榜新上架"。 */
export interface PublisherItunesArtist {
  id: number
  artist_id: string
  platform: 'ios' | 'gp'
  label: string | null
  last_synced_at: IsoDateString | null
}

export interface PublisherItunesArtistCreate {
  artist_id: string
  platform?: 'ios' | 'gp'
  label?: string | null
}

/** 调研出处类型。前四类 = 一手（primary），后四类 = 二手（secondary）。 */
export type PublisherSourceType =
  | 'registry' | 'official_filing' | 'official_platform' | 'official_domain'
  | 'media' | 'reference' | 'analysis' | 'self_report'

/** 主体溯源档位：有一手源 / 仅二手 / 未溯源。 */
export type ProvenanceTier = 'primary' | 'secondary' | 'none'

export interface PublisherSource {
  id: number
  url: string
  title: string | null
  source_type: PublisherSourceType
  is_primary: boolean
  confidence: string | null
  as_of: string | null
  note: string | null
}

export interface PublisherSourceCreate {
  url: string
  title?: string | null
  source_type: PublisherSourceType
  confidence?: string | null
  as_of?: string | null
  note?: string | null
}

/** 股权关系强度：全资 / 控股 / 参股 / 关联。 */
export type PublisherRelationType = 'wholly_owned' | 'controlling' | 'minority' | 'affiliate'
/** 对方相对本主体的角色：母公司 / 子公司。 */
export type RelationCounterpartRole = 'parent' | 'child'

/** 从某主体视角看到的一条股权关系（对方主体名已解析）。 */
export interface PublisherRelationLink {
  relation_id: number
  entity_id: number
  name: string
  relation_type: PublisherRelationType
  stake_pct: number | null
  note: string | null
}

export interface PublisherRelationCreate {
  counterpart_id: number
  counterpart_role: RelationCounterpartRole
  relation_type: PublisherRelationType
  stake_pct?: number | null
  note?: string | null
}

export interface PublisherEntity {
  id: number
  name: string
  name_en: string | null
  hq_region: string | null
  is_slg: boolean
  brief: string | null
  sort_order: number
  aliases: PublisherAlias[]
  app_ids: PublisherAppId[]
  itunes_artists: PublisherItunesArtist[]
  sources: PublisherSource[]
  provenance_tier: ProvenanceTier
  /** 本主体的母公司/投资方 */
  parents: PublisherRelationLink[]
  /** 本主体的子公司/关联 */
  children: PublisherRelationLink[]
  /** 旗下产品数；列表 / 详情视图均填 */
  product_count: number | null
  /** 折叠态图标锚点：旗下产品按收入降序的前 3 个（icon 来自 game_rankings，零 ST） */
  top_products: PublisherTopProduct[]
  /** 旗下产品在各市场最新快照里的最佳（最小）畅销榜名次；无上榜产品为 null */
  best_rank: number | null
  /** best_rank 命中的市场，如 "JP/android" */
  best_rank_market: string | null
  created_at: IsoDateString
  updated_at: IsoDateString
}

export interface PublisherTopProduct {
  app_id: string
  name: string | null
  icon_url: string | null
}

export interface PublisherAliasCreate {
  keyword: string
  label?: string | null
}

export interface PublisherAppIdCreate {
  app_id: string
  note?: string | null
}

export interface PublisherEntityCreate {
  name: string
  name_en?: string | null
  hq_region?: string | null
  is_slg?: boolean
  brief?: string | null
  sort_order?: number
  aliases?: PublisherAliasCreate[]
  app_ids?: PublisherAppIdCreate[]
}

export interface PublisherEntityUpdate {
  name?: string
  name_en?: string | null
  hq_region?: string | null
  is_slg?: boolean
  brief?: string | null
  sort_order?: number
}

/** 主体旗下某产品：跨已监测市场窗口内合计下载/收入，零 ST 配额。 */
export interface PublisherProduct {
  app_id: string
  name: string | null
  publisher: string | null
  icon_url: string | null
  downloads: number
  revenue: number
  matched_by: 'alias' | 'app_id' | 'radar'
  /** 雷达产品的子品类；无榜单 publisher 时作副标题兜底 */
  genre: string | null
}

export interface SyncRankingsResponse {
  message: string
  country: string
  platform: string
}

export interface SyncHistoryResponse {
  message: string
}

export interface SeedResponse {
  message: string
}

/** 订阅的行业公众号（新品监测日报按这些号搜文章）。 */
export interface WechatAccount {
  id: number
  name: string
  fakeid: string
  enabled: boolean
}

/** searchbiz 按名搜出的候选号（供选「订阅哪个」）。 */
export interface WechatAccountCandidate {
  fakeid: string
  nickname: string
  alias: string | null
}
