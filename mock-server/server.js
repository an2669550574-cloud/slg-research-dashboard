const http = require('http')

const GAMES = [
  { app_id: 'com.lilithgames.rok', name: 'Rise of Kingdoms', publisher: 'Lilith Games', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/c4/6e/2b/c46e2b1f-1c97-3b53-5b76-b9e6a3e06f44/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 1, downloads: 78200, revenue: 2150000 },
  { app_id: 'com.supercell.clashofclans', name: 'Clash of Clans', publisher: 'Supercell', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/6e/b4/1e/6eb41e98-2b90-7e07-f72f-c8d13be07d65/AppIcon-0-0-1x_U007emarketing-0-0-0-10-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 2, downloads: 65100, revenue: 1870000 },
  { app_id: 'com.igg.mobile.lordsmobile', name: 'Lords Mobile', publisher: 'IGG.COM', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/98/a1/b3/98a1b347-f5e9-e9f9-7e8d-8a0af0f82c8a/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 3, downloads: 52400, revenue: 1340000 },
  { app_id: 'com.machines.atwar', name: 'Whiteout Survival', publisher: 'Century Games', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/f2/6a/22/f26a22b1-d4c1-af5d-f23e-a2c53a8e4741/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 4, downloads: 48800, revenue: 1120000 },
  { app_id: 'com.diandian.lastwar', name: 'Last War: Survival', publisher: 'First Fun', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/2a/5f/4c/2a5f4c6d-8b52-0a04-0d1e-8fae1a0e8d12/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 5, downloads: 44200, revenue: 980000 },
  { app_id: 'com.century.games.warpath', name: 'Warpath', publisher: 'Lilith Games', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/6f/5e/0c/6f5e0c31-1b5c-7e92-2e1c-ebc8e9e0dd88/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 6, downloads: 36700, revenue: 760000 },
  { app_id: 'com.topgames.worldwar', name: 'Top War: Battle Game', publisher: 'Topgames.Inc', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/17/fc/ef/17fcef5c-cfae-b06e-e3fe-ee31e5c37eec/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 7, downloads: 28900, revenue: 530000 },
  { app_id: 'com.plarium.vikings', name: 'Vikings: War of Clans', publisher: 'Plarium', icon_url: 'https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/57/0b/40/570b401d-3a42-2c54-5e57-df67c37e1219/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg', rank: 8, downloads: 21300, revenue: 410000 },
]

const HISTORIES = {
  'com.lilithgames.rok': [
    { id: 1, event_date: '2018-09-17', event_type: 'launch', title: 'Rise of Kingdoms 全球公测上线', description: '前身为 Civilizations: Rise to Power，正式以 Rise of Kingdoms 为名全球上线，首月即进入美国策略榜 Top 10。' },
    { id: 2, event_date: '2019-03-01', event_type: 'marketing', title: '启用 KOL 营销矩阵', description: '与 YouTube 百万级频道合作，游戏实况和攻略视频累计播放量破亿，带动全球下载量环比增长 40%。' },
    { id: 3, event_date: '2020-07-01', event_type: 'revenue', title: '累计收入突破 10 亿美元', description: '成为 Lilith 旗下首款收入破 10 亿的产品，跻身全球手游收入 Top 20。' },
    { id: 4, event_date: '2021-06-01', event_type: 'ranking', title: '连续 30 天登顶美国策略榜', description: '借助赛季制内容更新和世界大战活动，在美国 App Store 策略分类连续 30 天排名第一。' },
    { id: 5, event_date: '2023-01-01', event_type: 'version', title: 'Lost Kingdom 全球同服新玩法上线', description: '上线跨服战争新地图，日活峰值创历史新高，单月内购收入超 4000 万美元。' },
  ],
  'com.supercell.clashofclans': [
    { id: 6, event_date: '2012-08-02', event_type: 'launch', title: 'Clash of Clans 芬兰软启动', description: 'Supercell 在芬兰及加拿大进行软启动测试，积累核心用户和数据反馈。' },
    { id: 7, event_date: '2012-10-07', event_type: 'launch', title: '全球正式上线 iOS', description: 'App Store 全球发布，首周即冲上多国免费榜榜首，成为现象级产品。' },
    { id: 8, event_date: '2015-02-01', event_type: 'marketing', title: '超级碗 60 秒广告亮相', description: 'Liam Neeson 出镜，播出后 App Store 排名从第 6 跃升至第 1，下载量单日暴涨 30 万次。' },
    { id: 9, event_date: '2016-01-01', event_type: 'revenue', title: '年收入突破 15 亿美元', description: '2015 全年营收达 15.3 亿美元，成为全球手游收入最高产品之一。' },
    { id: 10, event_date: '2021-04-01', event_type: 'version', title: 'Town Hall 14 与宠物系统上线', description: '引入宠物系统重磅更新，老玩家回流率创近三年新高，MAU 环比增长 18%。' },
  ],
}

const DEFAULT_HISTORY = [
  { id: 99, event_date: '2020-01-01', event_type: 'launch', title: '游戏全球公测上线', description: '产品在海外各大应用商店正式上线，开启全球市场推广。' },
  { id: 100, event_date: '2021-03-01', event_type: 'ranking', title: '进入美国策略榜 Top 20', description: '产品在北美市场取得重大突破，排名持续攀升。' },
]

const MATERIALS = []
let materialIdCounter = 1

function rand(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min }

function makeTrend(base, days) {
  const result = []
  let v = base
  const now = new Date()
  for (let i = days; i >= 0; i--) {
    const d = new Date(now); d.setDate(d.getDate() - i)
    v = Math.max(1, v * (1 + (Math.random() - 0.5) * 0.15))
    result.push({ date: d.toISOString().slice(0, 10), value: Math.round(v) })
  }
  return result
}

function makeRankingTrend(days) {
  const result = []
  let rank = rand(2, 8)
  const now = new Date()
  for (let i = days; i >= 0; i--) {
    const d = new Date(now); d.setDate(d.getDate() - i)
    rank = Math.max(1, rank + rand(-2, 2))
    result.push({ date: d.toISOString().slice(0, 10), value: rank })
  }
  return result
}

function route(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*')
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type')
  res.setHeader('Content-Type', 'application/json')

  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }

  const url = new URL(req.url, 'http://localhost')
  const path = url.pathname

  let body = ''
  req.on('data', c => body += c)
  req.on('end', () => {
    const json = (data) => { res.writeHead(200); res.end(JSON.stringify(data)) }

    if (path === '/api/health') return json({ status: 'ok' })

    if (path === '/api/games/rankings') return json(GAMES)

    if (path === '/api/games/' || path === '/api/games') {
      if (req.method === 'GET') return json(GAMES)
      if (req.method === 'POST') return json({ ...JSON.parse(body), id: rand(100, 999) })
    }

    if (path === '/api/games/seed') return json({ message: '已初始化 8 款游戏' })

    const metricsMatch = path.match(/^\/api\/games\/(.+)\/metrics$/)
    if (metricsMatch) {
      const days = parseInt(url.searchParams.get('days') || '30')
      const game = GAMES.find(g => g.app_id === metricsMatch[1]) || GAMES[0]
      return json({
        rankings: makeRankingTrend(days),
        downloads: makeTrend(game.downloads, days),
        revenue: makeTrend(game.revenue, days),
      })
    }

    const gameMatch = path.match(/^\/api\/games\/(.+)$/)
    if (gameMatch) {
      const game = GAMES.find(g => g.app_id === gameMatch[1])
      return game ? json(game) : (res.writeHead(404), res.end('{}'))
    }

    if (path.startsWith('/api/history/sync/')) {
      if (req.method === 'POST') return json({ message: '已同步 5 条历程数据（Mock 模式）' })
    }

    const historyMatch = path.match(/^\/api\/history\/(\d+)$/)
    if (historyMatch && req.method === 'DELETE') return json({ message: 'deleted' })

    if (path.startsWith('/api/history/')) {
      const appId = path.replace('/api/history/', '')
      if (req.method === 'GET') return json(HISTORIES[appId] || DEFAULT_HISTORY)
      if (req.method === 'POST') {
        const data = JSON.parse(body)
        return json({ ...data, id: rand(200, 999) })
      }
    }

    if (path === '/api/materials/' || path === '/api/materials') {
      const appIdFilter = url.searchParams.get('app_id')
      if (req.method === 'GET') {
        const result = appIdFilter ? MATERIALS.filter(m => m.app_id === appIdFilter) : [...MATERIALS]
        return json(result)
      }
      if (req.method === 'POST') {
        const data = JSON.parse(body)
        const m = { ...data, id: materialIdCounter++, created_at: new Date().toISOString(), tags: data.tags || [] }
        MATERIALS.push(m)
        return json(m)
      }
    }

    const matMatch = path.match(/^\/api\/materials\/(\d+)$/)
    if (matMatch) {
      const id = parseInt(matMatch[1])
      if (req.method === 'DELETE') {
        const idx = MATERIALS.findIndex(m => m.id === id)
        if (idx !== -1) MATERIALS.splice(idx, 1)
        return json({ message: 'deleted' })
      }
      if (req.method === 'PUT') {
        const idx = MATERIALS.findIndex(m => m.id === id)
        if (idx !== -1) Object.assign(MATERIALS[idx], JSON.parse(body))
        return json(MATERIALS[idx] || {})
      }
    }

    res.writeHead(404); res.end('{}')
  })
}

const PORT = 8000
http.createServer(route).listen(PORT, () => {
  console.log(`Mock API server running at http://localhost:${PORT}`)
  console.log('Ready for frontend connection.')
})
