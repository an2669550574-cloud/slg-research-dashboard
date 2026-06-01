import axios, { AxiosError } from 'axios'
import toast from 'react-hot-toast'
import type {
  AppLookupResult,
  DeleteResponse,
  GameCreate,
  GameOut,
  GameUpdate,
  HistoryCreate,
  HistoryOut,
  MaterialAnalysisStatus,
  MaterialCreate,
  MaterialOut,
  MaterialUpdate,
  MaterialTagCount,
  OwnProduct,
  OwnProductCreate,
  OwnProductUpdate,
  OwnProductMaterial,
  OwnProductAnalyzeResult,
  MetricsOut,
  MetricsCoverage,
  AggregateLeaderboardOut,
  MovementsOut,
  PagedResponse,
  QuotaHistoryOut,
  QuotaInfo,
  RankingTodayOut,
  SeedResponse,
  SyncHistoryResponse,
  SyncRankingsResponse,
} from './types'

// 缺 X-Total-Count 头时 fallback 用 items.length（兼容老路由 / 测试夹具）
function withTotal<T>(headers: { 'x-total-count'?: string } | Record<string, unknown>, items: T[]): PagedResponse<T> {
  const raw = (headers as Record<string, unknown>)['x-total-count']
  const total = typeof raw === 'string' ? parseInt(raw, 10) : NaN
  return { items, total: Number.isFinite(total) ? total : items.length }
}

const apiKey = import.meta.env.VITE_API_KEY as string | undefined

const api = axios.create({
  baseURL: '/api',
  headers: apiKey ? { 'X-API-Key': apiKey } : {},
})

api.interceptors.response.use(
  r => r,
  (error: AxiosError<{ detail?: string }>) => {
    // 全局错误兜底；mutation 自己若已 toast 也无妨（react-hot-toast 会去重展示）
    const detail = error.response?.data?.detail
    const status = error.response?.status
    const msg = detail || error.message || '请求失败'
    toast.error(status ? `${status} · ${msg}` : msg)
    return Promise.reject(error)
  }
)

export interface GameListParams {
  platform?: string
  country?: string
  publisher?: string
  q?: string
  sort_by?: 'name' | 'publisher' | 'release_date' | 'created_at' | 'updated_at'
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

export interface MetricsParams {
  days?: number
  country?: string
  platform?: string
  start_date?: string
  end_date?: string
  aggregate?: boolean
  /** 把同款 iOS+Android 姐妹 app_id 一起合并，避免详情页只看到当前 app_id 的半边 */
  merge_siblings?: boolean
}

export const gamesApi = {
  list: (params?: GameListParams): Promise<GameOut[]> =>
    api.get('/games/', { params }).then(r => r.data),
  listPaged: (params?: GameListParams): Promise<PagedResponse<GameOut>> =>
    api.get('/games/', { params }).then(r => withTotal(r.headers, r.data)),
  rankings: (country = 'US', platform = 'ios'): Promise<RankingTodayOut[]> =>
    api.get('/games/rankings', { params: { country, platform } }).then(r => r.data),
  get: (appId: string): Promise<GameOut> =>
    api.get(`/games/${appId}`).then(r => r.data),
  coverage: (appId: string, opts: { mergeSiblings?: boolean } = {}): Promise<MetricsCoverage[]> =>
    api.get(`/games/${appId}/coverage`, {
      params: opts.mergeSiblings ? { merge_siblings: true } : undefined,
    }).then(r => r.data),
  aggregateLeaderboard: (params: { days?: number; slg_only?: boolean; limit?: number } = {}): Promise<AggregateLeaderboardOut[]> =>
    api.get('/games/aggregate-leaderboard', { params }).then(r => r.data),
  metrics: (appId: string, params: MetricsParams = {}): Promise<MetricsOut> =>
    api.get(`/games/${appId}/metrics`, { params: { country: 'WW', days: 30, ...params } }).then(r => r.data),
  seed: (): Promise<SeedResponse> => api.get('/games/seed').then(r => r.data),
  create: (data: GameCreate): Promise<GameOut> =>
    api.post('/games/', data).then(r => r.data),
  update: (appId: string, data: GameUpdate): Promise<GameOut> =>
    api.put(`/games/${appId}`, data).then(r => r.data),
  delete: (appId: string): Promise<DeleteResponse> =>
    api.delete(`/games/${appId}`).then(r => r.data),
  lookup: (appId: string, country = 'us'): Promise<AppLookupResult> =>
    api.post('/games/lookup', null, { params: { app_id: appId, country } }).then(r => r.data),
  syncRankings: (country = 'US', platform = 'ios'): Promise<SyncRankingsResponse> =>
    api.post('/games/sync-rankings', null, { params: { country, platform } }).then(r => r.data),
  // dashboard "刷新数据"按钮专用：绕过 L1+L2 缓存强制重拉，会消耗一次月度配额
  refreshRankings: (country = 'US', platform = 'ios'): Promise<RankingTodayOut[]> =>
    api.post('/games/rankings/refresh', null, { params: { country, platform } }).then(r => r.data),
}

export const historyApi = {
  get: (appId: string): Promise<HistoryOut[]> =>
    api.get(`/history/${appId}`).then(r => r.data),
  sync: (appId: string): Promise<SyncHistoryResponse> =>
    api.post(`/history/sync/${appId}`).then(r => r.data),
  create: (data: HistoryCreate): Promise<HistoryOut> =>
    api.post('/history/', data).then(r => r.data),
  delete: (id: number): Promise<DeleteResponse> =>
    api.delete(`/history/${id}`).then(r => r.data),
}

export const quotaApi = {
  get: (): Promise<QuotaInfo> => api.get('/quota/').then(r => r.data),
  history: (days = 30): Promise<QuotaHistoryOut> =>
    api.get('/quota/history', { params: { days } }).then(r => r.data),
}

export const movementsApi = {
  /** 今日大事：可选传 country+platform 限定单组合，否则汇总全部 SYNC_RANKING_COMBOS */
  get: (opts: { country?: string; platform?: string } = {}): Promise<MovementsOut> =>
    api.get('/movements/', { params: opts }).then(r => r.data),
}

export interface MaterialListParams {
  platform?: string
  material_type?: string
  tag?: string
  q?: string
  analysis_status?: MaterialAnalysisStatus
  sort_by?: 'created_at' | 'title' | 'analyzed_at' | 'analysis_cost_usd'
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

export const materialsApi = {
  list: (appId?: string, params?: MaterialListParams): Promise<MaterialOut[]> =>
    api.get('/materials/', { params: { ...(appId ? { app_id: appId } : {}), ...params } }).then(r => r.data),
  listPaged: (params?: MaterialListParams & { app_id?: string }): Promise<PagedResponse<MaterialOut>> =>
    api.get('/materials/', { params }).then(r => withTotal(r.headers, r.data)),
  tags: (appId?: string): Promise<MaterialTagCount[]> =>
    api.get('/materials/tags', { params: appId ? { app_id: appId } : {} }).then(r => r.data),
  create: (data: MaterialCreate): Promise<MaterialOut> =>
    api.post('/materials/', data).then(r => r.data),
  upload: (form: FormData, onProgress?: (pct: number) => void): Promise<MaterialOut> =>
    api.post('/materials/upload', form, {
      onUploadProgress: e => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    }).then(r => r.data),
  update: (id: number, data: MaterialUpdate): Promise<MaterialOut> =>
    api.put(`/materials/${id}`, data).then(r => r.data),
  delete: (id: number): Promise<DeleteResponse> =>
    api.delete(`/materials/${id}`).then(r => r.data),
  get: (id: number): Promise<MaterialOut> =>
    api.get(`/materials/${id}`).then(r => r.data),
  analyze: (id: number): Promise<MaterialOut> =>
    api.post(`/materials/${id}/analyze`).then(r => r.data),
  adoptTags: (id: number): Promise<MaterialOut> =>
    api.post(`/materials/${id}/adopt-tags`).then(r => r.data),
  adaptDirections: (id: number, ourProduct: string) =>
    api.post(`/materials/${id}/adapt/directions`, { our_product: ourProduct }).then(r => r.data),
  adaptScript: (id: number, ourProduct: string, direction: unknown) =>
    api.post(`/materials/${id}/adapt/script`, { our_product: ourProduct, direction }).then(r => r.data),
  // 跨素材统一方向：估算成本（干跑，不烧配额）
  unifiedDirectionsEstimate: (
    materialIds: number[], ourProduct: string, model: AdaptModel,
  ): Promise<UnifiedCostEstimate> =>
    api.post('/materials/adapt/unified-directions', { material_ids: materialIds, our_product: ourProduct, model },
      { params: { estimate_only: true } }).then(r => r.data),
  // 跨素材统一方向：真实生成
  unifiedDirections: (
    materialIds: number[], ourProduct: string, model: AdaptModel,
  ): Promise<{ data: unknown; cost_usd: number; model: string }> =>
    api.post('/materials/adapt/unified-directions', { material_ids: materialIds, our_product: ourProduct, model }).then(r => r.data),
}

export const productsApi = {
  list: (): Promise<OwnProduct[]> => api.get('/products/').then(r => r.data),
  create: (data: OwnProductCreate): Promise<OwnProduct> =>
    api.post('/products/', data).then(r => r.data),
  update: (id: number, data: OwnProductUpdate): Promise<OwnProduct> =>
    api.put(`/products/${id}`, data).then(r => r.data),
  delete: (id: number): Promise<DeleteResponse> =>
    api.delete(`/products/${id}`).then(r => r.data),
  // ── 自有产品素材（AI 反推产品 brief 用）──
  materials: (productId: number): Promise<OwnProductMaterial[]> =>
    api.get(`/products/${productId}/materials`).then(r => r.data),
  uploadMaterial: (productId: number, form: FormData, onProgress?: (pct: number) => void): Promise<OwnProductMaterial> =>
    api.post(`/products/${productId}/materials/upload`, form, {
      onUploadProgress: e => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    }).then(r => r.data),
  addTextMaterial: (productId: number, data: { title?: string; text_content: string }): Promise<OwnProductMaterial> =>
    api.post(`/products/${productId}/materials/text`, data).then(r => r.data),
  deleteMaterial: (productId: number, materialId: number): Promise<DeleteResponse> =>
    api.delete(`/products/${productId}/materials/${materialId}`).then(r => r.data),
  analyze: (productId: number): Promise<OwnProductAnalyzeResult> =>
    api.post(`/products/${productId}/analyze`).then(r => r.data),
}

export type AdaptModel = 'claude-sonnet-4.5' | 'claude-opus-4.7'

export interface UnifiedCostEstimate {
  estimated_cost_usd: number
  model: string
  input_tokens_est: number
  output_tokens_est: number
  material_count: number
}
