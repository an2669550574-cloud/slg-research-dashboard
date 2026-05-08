# Frontend

React 18 + TypeScript + Vite + Tailwind。参考根目录 [README](../README.md) 获取整体说明。

## 起步

```bash
npm install
npm run dev    # http://localhost:3000
```

Vite 把 `/api/*` 代理到 `localhost:8000`。

## 构建

```bash
npm run build  # 跑 tsc -b 后用 vite build
```

## 目录

```
src/
├── App.tsx              # 路由 + 侧边栏 + 主题/语言切换
├── main.tsx             # ReactDOM 入口、QueryClient、Toaster
├── index.css            # Tailwind + CSS 变量（明暗主题）
├── lib/
│   ├── api.ts           # axios 客户端 + 错误拦截器 + 自动注入 X-API-Key
│   ├── csv.ts           # CSV 导出（带注入防御 + UTF-8 BOM）
│   ├── theme.ts         # useTheme() hook + localStorage
│   └── utils.ts         # formatNumber / formatRevenue / EVENT_TYPE_CONFIG
├── i18n/                # zh / en 强类型字典 + useT() hook
└── pages/
    ├── Dashboard.tsx
    ├── Rankings.tsx
    ├── GameDetail.tsx   # 趋势图 + 时间轴 + 素材
    ├── GamesManage.tsx  # 增删 + iTunes 预览
    ├── Compare.tsx      # 多游戏趋势叠加
    └── Materials.tsx
```

## 设计约定

- **颜色 token**：`bg-base / bg-surface / bg-elevated / border-default` 通过 CSS 变量随主题切换；现有 `bg-gray-XXX` 在 `index.css` 中有 light 主题兼容层覆盖，逐步替换为 token
- **请求**：所有 mutation 写 `onSuccess` 时主动 `qc.invalidateQueries`；用 `react-hot-toast` 给用户即时反馈
- **导出**：用 `lib/csv.ts` 的 `downloadCsv()`，列名走 `i18n` 的 `csv.*` 字典
- **新增页面**：在 `App.tsx` 加 Route + NAV 项，文案统一通过 `useT()`

## 环境变量

```
VITE_API_KEY=<和后端 API_KEY 相同的值>
```
