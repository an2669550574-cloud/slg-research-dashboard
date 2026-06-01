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

export interface MaterialCreate {
  app_id: string
  title: string
  url: string
  platform?: string | null
  material_type?: string
  tags?: string[]
  notes?: string | null
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
