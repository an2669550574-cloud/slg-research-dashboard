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

export interface MaterialOut {
  id: number
  app_id: string
  title: string
  url: string
  platform: string | null
  material_type: string
  tags: string[]
  notes: string | null
  created_at: IsoDateString
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

export type MaterialUpdate = Partial<Omit<MaterialCreate, 'app_id'>>

// ─── quota ───────────────────────────────────────────────────────────────

export type DataSource = 'real_api' | 'mock' | 'snapshot_stale'

export interface QuotaInfo {
  year_month: string
  used: number
  limit: number
  remaining: number
  percentage: number
  exhausted: boolean
  data_source?: DataSource
  data_updated_at?: string | null
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

export interface DeleteResponse {
  message: string
  app_id?: string
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
