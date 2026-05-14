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
  MaterialCreate,
  MaterialOut,
  MaterialUpdate,
  MetricsOut,
  PagedResponse,
  QuotaInfo,
  RankingTodayOut,
  SeedResponse,
  SyncHistoryResponse,
  SyncRankingsResponse,
} from './types'

/** 把 axios 响应包成 {items, total}，total 从 X-Total-Count 头里读。
 * 缺这个头时 fallback 用 items.length（兼容老路由 / 测试夹具）。
 */
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
}

export const gamesApi = {
  list: (params?: GameListParams): Promise<GameOut[]> =>
    api.get('/games/', { params }).then(r => r.data),
  /** 带总数的分页查询。GamesManage 用，能正确展示 "X / total" 和翻页。 */
  listPaged: (params?: GameListParams): Promise<PagedResponse<GameOut>> =>
    api.get('/games/', { params }).then(r => withTotal(r.headers, r.data)),
  rankings: (country = 'US', platform = 'ios'): Promise<RankingTodayOut[]> =>
    api.get('/games/rankings', { params: { country, platform } }).then(r => r.data),
  get: (appId: string): Promise<GameOut> =>
    api.get(`/games/${appId}`).then(r => r.data),
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
}

export interface MaterialListParams {
  platform?: string
  material_type?: string
  q?: string
  sort_by?: 'created_at' | 'title'
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

export const materialsApi = {
  list: (appId?: string, params?: MaterialListParams): Promise<MaterialOut[]> =>
    api.get('/materials/', { params: { ...(appId ? { app_id: appId } : {}), ...params } }).then(r => r.data),
  listPaged: (params?: MaterialListParams & { app_id?: string }): Promise<PagedResponse<MaterialOut>> =>
    api.get('/materials/', { params }).then(r => withTotal(r.headers, r.data)),
  create: (data: MaterialCreate): Promise<MaterialOut> =>
    api.post('/materials/', data).then(r => r.data),
  update: (id: number, data: MaterialUpdate): Promise<MaterialOut> =>
    api.put(`/materials/${id}`, data).then(r => r.data),
  delete: (id: number): Promise<DeleteResponse> =>
    api.delete(`/materials/${id}`).then(r => r.data),
}
