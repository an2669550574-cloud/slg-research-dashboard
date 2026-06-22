import axios, { AxiosError } from 'axios'
import toast from 'react-hot-toast'
import type {
  AppLookupResult,
  CreativeAdaptationOut,
  CreativeDirectionsResponse,
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
  NewcomersOut,
  NewcomerHistoryOut,
  PublisherNewcomersOut,
  AppstoreReleasesOut,
  PublisherItunesArtist,
  PublisherItunesArtistCreate,
  PagedResponse,
  QuotaHistoryOut,
  QuotaInfo,
  RankingTodayOut,
  SeedResponse,
  SyncHistoryResponse,
  SyncRankingsResponse,
  TagDimension,
  TagDimensionCreate,
  TagDimensionUpdate,
  TagOption,
  TagOptionCreate,
  TagOptionUpdate,
  TagDeleteResponse,
  TagAggregateOut,
  TagAggregateParams,
  MaterialTagValueInput,
  TagAnalysisSession,
  TagAnalysisSessionListItem,
  TagAnalysisRunRequest,
  TagAnalysisEstimate,
  PublisherEntity,
  PublisherEntityCreate,
  PublisherEntityUpdate,
  PublisherAlias,
  PublisherAliasCreate,
  PublisherAppId,
  PublisherAppIdCreate,
  PublisherSource,
  PublisherSourceCreate,
  PublisherRelationLink,
  PublisherRelationCreate,
  PublisherProduct,
  PublisherHealth,
  WechatAccount,
  WechatAccountCandidate,
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

export const newcomersApi = {
  /** 新品监测：近期首次进榜的新面孔。可选传 country+platform 限定单组合，
   *  否则汇总全部 SYNC_RANKING_COMBOS。零 ST 配额，纯本地榜单比对。 */
  get: (opts: { country?: string; platform?: string } = {}): Promise<NewcomersOut> =>
    api.get('/newcomers/', { params: opts }).then(r => r.data),
  /** 厂商新品：已建档主体的产品首次出现在已监测榜单（任意名次），跨全部 combo 汇总。零配额。 */
  publishers: (): Promise<PublisherNewcomersOut> =>
    api.get('/newcomers/publishers').then(r => r.data),
  /** 新面孔检出历史：检出即落库 + 免费源富化（iTunes/GP），可按市场/平台/名次/信号类型筛。
   *  signal='true_new'(默认) 仅真首发；'reentry' 仅回归；'all' 全部不筛。 */
  history: (opts: { days?: number; country?: string; platform?: string; topn?: number; signal?: 'true_new' | 'reentry' | 'all' } = {}): Promise<NewcomerHistoryOut> =>
    api.get('/newcomers/history', { params: opts }).then(r => r.data),
  /** 手动触发全 combo 检出落库（首次回填用）。 */
  historySync: (): Promise<{ message: string; detected: number; recorded: number; enriched: number }> =>
    api.post('/newcomers/history/sync').then(r => r.data),
  /** App Store 新上架：开发者账号清单 diff（免费 iTunes API），不依赖进榜。 */
  appstore: (days = 60): Promise<AppstoreReleasesOut> =>
    api.get('/newcomers/appstore', { params: { days } }).then(r => r.data),
  /** 手动触发一轮清单同步（首次挂账号建基线用；平时靠周级调度）。 */
  appstoreSync: (): Promise<{ message: string; synced: number; failed: number; baselined: number; new_apps: number }> =>
    api.post('/newcomers/appstore/sync').then(r => r.data),
}

export interface MaterialListParams {
  platform?: string
  material_type?: string
  tag?: string
  tag_options?: string
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
  // 结构化打标签（P2）：整体替换某素材的标签值（replace-all）；必填/单多选校验在后端
  setTagValues: (id: number, values: MaterialTagValueInput[]): Promise<MaterialOut> =>
    api.put(`/materials/${id}/tag-values`, { values }).then(r => r.data),
  analyze: (id: number): Promise<MaterialOut> =>
    api.post(`/materials/${id}/analyze`).then(r => r.data),
  adoptTags: (id: number): Promise<MaterialOut> =>
    api.post(`/materials/${id}/adopt-tags`).then(r => r.data),
  adaptDirections: (id: number, ourProduct: string, productId?: number | null): Promise<CreativeDirectionsResponse> =>
    api.post(`/materials/${id}/adapt/directions`, { our_product: ourProduct, product_id: productId ?? null }).then(r => r.data),
  adaptScript: (id: number, ourProduct: string, direction: unknown, adaptationId?: number | null, directionIndex?: number | null) =>
    api.post(`/materials/${id}/adapt/script`, {
      our_product: ourProduct, direction,
      adaptation_id: adaptationId ?? null, direction_index: directionIndex ?? null,
    }).then(r => r.data),
  // 创意迁移历史存档：列出 / 删除（零 LLM 开销，纯本地库）
  listAdaptations: (id: number): Promise<CreativeAdaptationOut[]> =>
    api.get(`/materials/${id}/adaptations`).then(r => r.data),
  deleteAdaptation: (adaptationId: number): Promise<DeleteResponse> =>
    api.delete(`/materials/adaptations/${adaptationId}`).then(r => r.data),
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

// 删除一级 / 二级标签时附带管理员口令（后端未配置口令则忽略此头）。
// 口令不进前端构建，运行时由调用方弹框收集后传入。
function adminHeader(password?: string | null) {
  return password ? { headers: { 'X-Admin-Password': password } } : undefined
}

export const tagsApi = {
  listDimensions: (materialType?: string): Promise<TagDimension[]> =>
    api.get('/tags/dimensions', { params: materialType ? { material_type: materialType } : {} }).then(r => r.data),
  createDimension: (data: TagDimensionCreate): Promise<TagDimension> =>
    api.post('/tags/dimensions', data).then(r => r.data),
  updateDimension: (id: number, data: TagDimensionUpdate): Promise<TagDimension> =>
    api.put(`/tags/dimensions/${id}`, data).then(r => r.data),
  deleteDimension: (id: number, password?: string | null): Promise<TagDeleteResponse> =>
    api.delete(`/tags/dimensions/${id}`, adminHeader(password)).then(r => r.data),
  createOption: (dimId: number, data: TagOptionCreate): Promise<TagOption> =>
    api.post(`/tags/dimensions/${dimId}/options`, data).then(r => r.data),
  updateOption: (optId: number, data: TagOptionUpdate): Promise<TagOption> =>
    api.put(`/tags/options/${optId}`, data).then(r => r.data),
  deleteOption: (optId: number, password?: string | null): Promise<TagDeleteResponse> =>
    api.delete(`/tags/options/${optId}`, adminHeader(password)).then(r => r.data),
  aggregate: (params: TagAggregateParams): Promise<TagAggregateOut> =>
    api.get('/tags/aggregate', { params }).then(r => r.data),
}

// AI 标签分析 Agent（P6）：跑报告 / 追问、会话回查、导出 md·csv。走公司网关，零 ST 配额。
export const tagAnalysisApi = {
  run: (data: TagAnalysisRunRequest): Promise<TagAnalysisSession> =>
    api.post('/tags/analysis', data).then(r => r.data),
  list: (): Promise<TagAnalysisSessionListItem[]> =>
    api.get('/tags/analysis').then(r => r.data),
  get: (id: number): Promise<TagAnalysisSession> =>
    api.get(`/tags/analysis/${id}`).then(r => r.data),
  // 成本干跑预估：当前范围跑一次报告约花多少（不打网关、零配额）。供模型下拉旁「约 $X」。
  estimate: (params: { model: string; app_id?: string; material_type?: string; tag_options?: string }): Promise<TagAnalysisEstimate> =>
    api.get('/tags/analysis/estimate', { params }).then(r => r.data),
  del: (id: number): Promise<DeleteResponse> =>
    api.delete(`/tags/analysis/${id}`).then(r => r.data),
  // 导出走 axios（带 X-API-Key），拿 blob 后手动触发下载（<a download> 带不了鉴权头）。
  exportFile: async (id: number, fmt: 'md' | 'csv'): Promise<void> => {
    const r = await api.get(`/tags/analysis/${id}/export.${fmt}`, { responseType: 'blob' })
    const url = URL.createObjectURL(r.data as Blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `tag-analysis-${id}.${fmt}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
}

// 厂商主体（publisher entities）：主体 CRUD + 马甲/app_id 子资源 + 旗下产品聚合。
// 三表是 is_slg 判定的唯一数据源；写操作后端会即时刷新内存索引。零 ST 配额。
export const publishersApi = {
  list: (): Promise<PublisherEntity[]> =>
    api.get('/publishers/').then(r => r.data),
  get: (id: number): Promise<PublisherEntity> =>
    api.get(`/publishers/${id}`).then(r => r.data),
  create: (data: PublisherEntityCreate): Promise<PublisherEntity> =>
    api.post('/publishers/', data).then(r => r.data),
  update: (id: number, data: PublisherEntityUpdate): Promise<PublisherEntity> =>
    api.put(`/publishers/${id}`, data).then(r => r.data),
  delete: (id: number): Promise<DeleteResponse> =>
    api.delete(`/publishers/${id}`).then(r => r.data),
  addAlias: (id: number, data: PublisherAliasCreate): Promise<PublisherAlias> =>
    api.post(`/publishers/${id}/aliases`, data).then(r => r.data),
  deleteAlias: (id: number, aliasId: number): Promise<DeleteResponse> =>
    api.delete(`/publishers/${id}/aliases/${aliasId}`).then(r => r.data),
  addAppId: (id: number, data: PublisherAppIdCreate): Promise<PublisherAppId> =>
    api.post(`/publishers/${id}/app-ids`, data).then(r => r.data),
  deleteAppId: (id: number, appIdRowId: number): Promise<DeleteResponse> =>
    api.delete(`/publishers/${id}/app-ids/${appIdRowId}`).then(r => r.data),
  // App Store 开发者账号（iTunes artistId）：清单 diff 抓未进榜新上架。免费 API、零 ST 配额。
  addItunesArtist: (id: number, data: PublisherItunesArtistCreate): Promise<PublisherItunesArtist> =>
    api.post(`/publishers/${id}/itunes-artists`, data).then(r => r.data),
  deleteItunesArtist: (id: number, artistRowId: number): Promise<DeleteResponse> =>
    api.delete(`/publishers/${id}/itunes-artists/${artistRowId}`).then(r => r.data),
  // 调研出处（一手源溯源）：增删；写后档案 provenance_tier 随之刷新。零 ST 配额。
  addSource: (id: number, data: PublisherSourceCreate): Promise<PublisherSource> =>
    api.post(`/publishers/${id}/sources`, data).then(r => r.data),
  deleteSource: (id: number, sourceId: number): Promise<DeleteResponse> =>
    api.delete(`/publishers/${id}/sources/${sourceId}`).then(r => r.data),
  // 股权/母子关系：从本主体视角增删（counterpart_role=parent 对方是母公司 / child 是子公司）。
  addRelation: (id: number, data: PublisherRelationCreate): Promise<PublisherRelationLink> =>
    api.post(`/publishers/${id}/relations`, data).then(r => r.data),
  deleteRelation: (id: number, relationId: number): Promise<DeleteResponse> =>
    api.delete(`/publishers/${id}/relations/${relationId}`).then(r => r.data),
  products: (id: number, days = 30): Promise<PublisherProduct[]> =>
    api.get(`/publishers/${id}/products`, { params: { days } }).then(r => r.data),
  // 数据健康度自检：覆盖 tier 分布 + 待补/命名/复核 backlog + 总量。
  health: (): Promise<PublisherHealth> =>
    api.get('/publishers/health').then(r => r.data),
}

// 订阅公众号：看板维护新品监测日报要搜哪些行业号。零 ST 配额（走 wechat-api）。
export const wechatAccountsApi = {
  list: (): Promise<WechatAccount[]> =>
    api.get('/wechat-accounts/').then(r => r.data),
  search: (query: string): Promise<WechatAccountCandidate[]> =>
    api.get('/wechat-accounts/search', { params: { query } }).then(r => r.data),
  create: (data: { fakeid: string; name: string }): Promise<WechatAccount> =>
    api.post('/wechat-accounts/', data).then(r => r.data),
  setEnabled: (id: number, enabled: boolean): Promise<WechatAccount> =>
    api.patch(`/wechat-accounts/${id}`, { enabled }).then(r => r.data),
  remove: (id: number): Promise<DeleteResponse> =>
    api.delete(`/wechat-accounts/${id}`).then(r => r.data),
}

export type AdaptModel = 'claude-sonnet-4.5' | 'claude-opus-4.7'

export interface UnifiedCostEstimate {
  estimated_cost_usd: number
  model: string
  input_tokens_est: number
  output_tokens_est: number
  material_count: number
}
