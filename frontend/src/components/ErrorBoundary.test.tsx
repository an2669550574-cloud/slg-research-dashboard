import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ErrorBoundary } from './ErrorBoundary'

function Boom({ throwIt }: { throwIt: boolean }): JSX.Element {
  if (throwIt) throw new Error('test boom')
  return <div>healthy child</div>
}

describe('ErrorBoundary', () => {
  let consoleSpy: ReturnType<typeof vi.spyOn>

  let onError: (e: ErrorEvent) => void

  beforeEach(() => {
    // React 抛错时会 console.error；jsdom 还会把它当成 window 'error' 事件冒泡。
    // 两条路径都吞掉，否则测试输出会被 React stack trace 淹没。
    consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    onError = (e: ErrorEvent) => e.preventDefault()
    window.addEventListener('error', onError)
  })

  afterEach(() => {
    consoleSpy.mockRestore()
    window.removeEventListener('error', onError)
  })

  it('renders children when there is no error', () => {
    render(
      <ErrorBoundary>
        <Boom throwIt={false} />
      </ErrorBoundary>
    )
    expect(screen.getByText('healthy child')).toBeInTheDocument()
  })

  it('shows fallback UI when a child throws', () => {
    render(
      <ErrorBoundary>
        <Boom throwIt={true} />
      </ErrorBoundary>
    )
    expect(screen.getByText('页面渲染出错')).toBeInTheDocument()
    expect(screen.getByText('Page failed to render')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('clicking 重试 clears the error and re-renders children', async () => {
    const user = userEvent.setup()
    // 第一次渲染抛错；点击重试后我们让它不再抛
    let throwIt = true
    function Toggle() {
      return <Boom throwIt={throwIt} />
    }

    const { rerender } = render(
      <ErrorBoundary>
        <Toggle />
      </ErrorBoundary>
    )
    expect(screen.getByText('页面渲染出错')).toBeInTheDocument()

    throwIt = false
    await user.click(screen.getByRole('button', { name: '重试' }))
    rerender(
      <ErrorBoundary>
        <Toggle />
      </ErrorBoundary>
    )
    expect(screen.getByText('healthy child')).toBeInTheDocument()
    expect(screen.queryByText('页面渲染出错')).not.toBeInTheDocument()
  })
})
