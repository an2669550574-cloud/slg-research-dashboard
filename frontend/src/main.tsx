import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, retry: 1 } }
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
