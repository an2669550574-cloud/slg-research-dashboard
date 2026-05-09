import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 后端缓存就是日级（24h），前端没必要比它频繁多少。
      // 5 分钟内同 query 不 refetch，用户切页面/Modal 几乎都靠缓存。
      staleTime: 5 * 60_000,
      retry: 1,
      // 切回标签页不再触发 refetch；Sensor Tower 数据本身一天一更，没必要这么神经质。
      refetchOnWindowFocus: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
        <Toaster
          position="top-right"
          toastOptions={{
            duration: 3500,
            style: {
              background: 'rgb(var(--bg-elevated))',
              color: 'rgb(var(--text-primary))',
              border: '1px solid rgb(var(--border-default))',
              fontSize: '13px',
            },
            success: { iconTheme: { primary: '#10b981', secondary: 'rgb(var(--bg-elevated))' } },
            error: { iconTheme: { primary: '#ef4444', secondary: 'rgb(var(--bg-elevated))' } },
          }}
        />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>
)
