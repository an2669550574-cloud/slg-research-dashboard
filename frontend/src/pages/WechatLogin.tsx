import { useEffect, useRef, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { wechatLoginApi } from '../lib/api'

// 扫码状态机：checking=查当前登录态 / qr=出码待扫 / scanned=已扫待手机确认 /
// success=登录成功 / expired=二维码过期 / error=出错 / logged_in=本就有效无需续期。
type Phase = 'checking' | 'qr' | 'scanned' | 'success' | 'expired' | 'error' | 'logged_in'

export default function WechatLogin() {
  const [phase, setPhase] = useState<Phase>('checking')
  const [msg, setMsg] = useState('正在检查当前登录态…')
  const [qrUrl, setQrUrl] = useState<string | null>(null)
  const [nickname, setNickname] = useState<string | null>(null)

  // 跨渲染保活的可变量：避免进 state 触发重渲染 / 闭包过期。
  const mounted = useRef(true)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const objUrl = useRef<string | null>(null)
  const sessionId = useRef('')

  function clearPoll() {
    if (pollTimer.current) { clearTimeout(pollTimer.current); pollTimer.current = null }
  }
  function schedule(fn: () => void, ms: number) {
    clearPoll()
    pollTimer.current = setTimeout(() => { if (mounted.current) fn() }, ms)
  }
  function setQr(blob: Blob) {
    if (objUrl.current) URL.revokeObjectURL(objUrl.current)
    objUrl.current = URL.createObjectURL(blob)
    setQrUrl(objUrl.current)
  }

  async function refreshQr() {
    setPhase('qr'); setMsg('正在加载二维码…')
    try {
      setQr(await wechatLoginApi.qrcodeBlob(Math.random()))
      if (!mounted.current) return
      setMsg('请用微信扫描二维码')
      schedule(poll, 2000)
    } catch {
      if (mounted.current) { setPhase('error'); setMsg('二维码加载失败，请点「刷新二维码」重试') }
    }
  }

  async function poll() {
    try {
      const data = await wechatLoginApi.scan()
      if (!mounted.current) return
      if (data?.base_resp && data.base_resp.ret !== 0) { schedule(poll, 3000); return }
      switch (data?.status ?? 0) {
        case 1:
          setPhase('scanned'); setMsg('扫码成功，正在登录…'); completeLogin(); break
        case 4: case 6:
          setMsg((data?.acct_size || 0) > 1 ? '扫码成功，请在手机上选择账号'
            : '扫码成功，请在手机上确认登录')
          schedule(poll, 1500); break
        case 2:
          setPhase('expired'); setMsg('二维码已过期，请点「刷新二维码」'); break
        case 3:
          setPhase('error'); setMsg('扫码失败，请点「刷新二维码」重试'); break
        default:
          schedule(poll, 3000)
      }
    } catch {
      if (mounted.current) schedule(poll, 3000)
    }
  }

  async function completeLogin() {
    try {
      const data = await wechatLoginApi.bizlogin()
      if (!mounted.current) return
      if (data?.success && data.data) {
        setPhase('success'); setNickname(data.data.nickname || null)
        setMsg(`登录成功${data.data.nickname ? ' — ' + data.data.nickname : ''}，登录态已续期`)
      } else {
        setPhase('error'); setMsg('登录失败：' + (data?.error || '未知错误'))
      }
    } catch {
      if (mounted.current) { setPhase('error'); setMsg('登录失败，请重试') }
    }
  }

  async function startLogin() {
    clearPoll()
    sessionId.current = `${Date.now()}${Math.floor(Math.random() * 100)}`
    try {
      await wechatLoginApi.session(sessionId.current)
    } catch {
      if (mounted.current) { setPhase('error'); setMsg('初始化登录会话失败，请刷新页面重试') }
      return
    }
    if (mounted.current) await refreshQr()
  }

  async function init() {
    setPhase('checking'); setMsg('正在检查当前登录态…')
    try {
      const s = await wechatLoginApi.status()
      if (!mounted.current) return
      if (s?.loggedIn && !s?.isExpired) {
        setPhase('logged_in'); setNickname(s.nickname || null)
        setMsg(`当前已登录${s.nickname ? '（' + s.nickname + '）' : ''}，登录态有效、无需续期`)
        return
      }
    } catch { /* 查不到当前态不致命，直接走扫码流程 */ }
    if (mounted.current) await startLogin()
  }

  useEffect(() => {
    mounted.current = true
    init()
    return () => {
      mounted.current = false
      clearPoll()
      if (objUrl.current) URL.revokeObjectURL(objUrl.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const showQr = phase === 'qr' || phase === 'scanned'
  const showRefresh = phase === 'expired' || phase === 'error'
  const statusColor =
    phase === 'success' || phase === 'logged_in' ? 'text-emerald-400'
      : phase === 'expired' || phase === 'error' ? 'text-red-400'
        : 'text-secondary'

  return (
    <div className="px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto space-y-5">
      <PageHeader
        eyebrow="WeChat Session"
        title="微信公众号续期"
        subtitle="扫码续期登录态——新品监测日报的「行业文章」段依赖它（其余情报不受影响）"
      />
      <div className="bg-surface border border-default rounded-xl p-6 max-w-md mx-auto flex flex-col items-center gap-5 text-center">
        <div className="w-[220px] h-[220px] flex items-center justify-center bg-elevated border border-default rounded-lg overflow-hidden">
          {showQr && qrUrl ? (
            <img src={qrUrl} alt="微信登录二维码" className="w-[200px] h-[200px] rounded" />
          ) : phase === 'success' || phase === 'logged_in' ? (
            <span className="text-5xl">✅</span>
          ) : (
            <span className="text-muted text-sm">{phase === 'checking' ? '检查中…' : '二维码区'}</span>
          )}
        </div>
        <p className={`text-sm font-medium ${statusColor}`}>{msg}</p>
        {showRefresh && (
          <button
            onClick={() => startLogin()}
            className="px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors"
          >
            🔄 刷新二维码
          </button>
        )}
        {phase === 'logged_in' && (
          <button
            onClick={() => startLogin()}
            className="px-3.5 py-2.5 rounded-lg font-data text-xs text-secondary border border-default hover:border-strong hover:text-primary bg-surface/60 transition-colors"
          >
            仍要重新登录
          </button>
        )}
        <p className="text-muted text-xs leading-relaxed">
          扫码后请在手机微信上确认登录。续期成功后日报会自动恢复附带行业文章。
        </p>
      </div>
    </div>
  )
}
