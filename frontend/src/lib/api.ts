import axios, { AxiosError } from 'axios'
import toast from 'react-hot-toast'

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

export const gamesApi = {
  list: (params?: Record<string, any>) => api.get('/games/', { params }).then(r => r.data),
  rankings: (country = 'US', platform = 'ios') =>
    api.get('/games/rankings', { params: { country, platform } }).then(r => r.data),
  get: (appId: string) => api.get(`/games/${appId}`).then(r => r.data),
  metrics: (appId: string, params: { days?: number; country?: string; start_date?: string; end_date?: string } = {}) =>
    api.get(`/games/${appId}/metrics`, { params: { country: 'WW', days: 30, ...params } }).then(r => r.data),
  seed: () => api.get('/games/seed').then(r => r.data),
  create: (data: any) => api.post('/games/', data).then(r => r.data),
  update: (appId: string, data: any) => api.put(`/games/${appId}`, data).then(r => r.data),
  delete: (appId: string) => api.delete(`/games/${appId}`).then(r => r.data),
  lookup: (appId: string, country = 'us') => api.post('/games/lookup', null, { params: { app_id: appId, country } }).then(r => r.data),
  syncRankings: (country = 'US', platform = 'ios') =>
    api.post('/games/sync-rankings', null, { params: { country, platform } }).then(r => r.data),
}

export const historyApi = {
  get: (appId: string) => api.get(`/history/${appId}`).then(r => r.data),
  sync: (appId: string) => api.post(`/history/sync/${appId}`).then(r => r.data),
  create: (data: any) => api.post('/history/', data).then(r => r.data),
  delete: (id: number) => api.delete(`/history/${id}`).then(r => r.data),
}

export const quotaApi = {
  get: () => api.get('/quota/').then(r => r.data) as Promise<{
    year_month: string
    used: number
    limit: number
    remaining: number
    percentage: number
    exhausted: boolean
  }>,
}

export const materialsApi = {
  list: (appId?: string, params?: Record<string, any>) =>
    api.get('/materials/', { params: { ...(appId ? { app_id: appId } : {}), ...params } }).then(r => r.data),
  create: (data: any) => api.post('/materials/', data).then(r => r.data),
  update: (id: number, data: any) => api.put(`/materials/${id}`, data).then(r => r.data),
  delete: (id: number) => api.delete(`/materials/${id}`).then(r => r.data),
}
