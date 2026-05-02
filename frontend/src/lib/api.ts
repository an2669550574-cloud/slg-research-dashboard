import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const gamesApi = {
  list: () => api.get('/games/').then(r => r.data),
  rankings: (country = 'US', platform = 'ios') =>
    api.get('/games/rankings', { params: { country, platform } }).then(r => r.data),
  get: (appId: string) => api.get(`/games/${appId}`).then(r => r.data),
  metrics: (appId: string, days = 30, country = 'WW') =>
    api.get(`/games/${appId}/metrics`, { params: { days, country } }).then(r => r.data),
  seed: () => api.get('/games/seed').then(r => r.data),
  create: (data: any) => api.post('/games/', data).then(r => r.data),
}

export const historyApi = {
  get: (appId: string) => api.get(`/history/${appId}`).then(r => r.data),
  sync: (appId: string) => api.post(`/history/sync/${appId}`).then(r => r.data),
  create: (data: any) => api.post('/history/', data).then(r => r.data),
  delete: (id: number) => api.delete(`/history/${id}`).then(r => r.data),
}

export const materialsApi = {
  list: (appId?: string) => api.get('/materials/', { params: appId ? { app_id: appId } : {} }).then(r => r.data),
  create: (data: any) => api.post('/materials/', data).then(r => r.data),
  update: (id: number, data: any) => api.put(`/materials/${id}`, data).then(r => r.data),
  delete: (id: number) => api.delete(`/materials/${id}`).then(r => r.data),
}
