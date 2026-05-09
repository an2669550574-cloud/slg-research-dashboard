import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
  errorInfo: ErrorInfo | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, errorInfo: null }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo })
    // 上报后端日志（如果有 Sentry 浏览器 SDK 就 sentry-sdk 替换 console.error）
    console.error('[ErrorBoundary]', error, errorInfo.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children

    const dev = import.meta.env.DEV
    return (
      <div className="min-h-screen flex items-center justify-center bg-base p-6">
        <div className="max-w-lg w-full bg-surface border border-default rounded-xl p-8 space-y-5">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-red-500/10 rounded-lg">
              <AlertTriangle className="text-red-400" size={20} />
            </div>
            <div>
              <h1 className="text-lg font-bold text-primary">页面渲染出错</h1>
              <p className="text-xs text-muted mt-0.5">Page failed to render</p>
            </div>
          </div>

          <p className="text-sm text-secondary leading-relaxed">
            前端遇到一个未捕获的异常。可以尝试刷新页面恢复；如果反复出现请把下面的错误信息贴给开发者。
          </p>

          {dev && (
            <details className="bg-base border border-default rounded-lg p-3 text-xs">
              <summary className="cursor-pointer text-secondary font-medium">错误详情（仅开发模式可见）</summary>
              <pre className="mt-2 text-red-300 whitespace-pre-wrap break-words font-mono text-[11px]">
                {this.state.error.message}
                {'\n\n'}
                {this.state.error.stack}
              </pre>
              {this.state.errorInfo && (
                <pre className="mt-2 text-muted whitespace-pre-wrap break-words font-mono text-[11px]">
                  {this.state.errorInfo.componentStack}
                </pre>
              )}
            </details>
          )}

          <div className="flex gap-2">
            <button
              onClick={() => { this.setState({ error: null, errorInfo: null }) }}
              className="flex items-center gap-1.5 px-3 py-2 bg-elevated hover:bg-elevated/70 rounded-lg text-sm text-primary transition-colors"
            >
              重试
            </button>
            <button
              onClick={() => window.location.reload()}
              className="flex items-center gap-1.5 px-3 py-2 bg-brand-600 hover:bg-brand-700 rounded-lg text-sm text-white transition-colors"
            >
              <RefreshCw size={14} />
              刷新页面
            </button>
          </div>
        </div>
      </div>
    )
  }
}
