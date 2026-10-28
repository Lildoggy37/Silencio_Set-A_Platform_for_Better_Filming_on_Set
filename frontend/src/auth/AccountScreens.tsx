import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Button, Input } from 'antd'
import { AccountError, trimName, validName, validNewPassword } from '../api/accounts'
import type { User, Verification } from '../api/accounts'
import { useAuth } from './AuthProvider'

class FormIssue extends Error {}

export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof AccountError || error instanceof FormIssue ? error.message : '操作结果未确认，请检查连接后再试；未自动重试。'
  return <div className="account-error" role="alert"><p>{message}</p>
    {error instanceof AccountError && error.requestId && <span className="request-id">请求标识 · {error.requestId}</span>}
  </div>
}

export function AccountFrame({ children, title, subtitle, actions }: { children: ReactNode; title: string; subtitle: string; actions?: ReactNode }) {
  return <>
    <a className="skip-link" href="#main">跳转到内容</a>
    <header className="topbar account-topbar"><div className="identity"><span className="monogram">SS</span><span>Silencio Set<small>寂静片场</small></span></div><div className="account-header-actions">{actions}</div></header>
    <main className="account-layout" id="main">
      <aside className="account-art" aria-label="SS · Silencio Set">
        <div className="account-wordmark" aria-hidden="true">{Array.from({ length: 15 }, (_, row) =>
          <div className="account-wordmark-row" key={row}>{Array.from({ length: 5 }, (_, word) => <span key={word}><b>SS</b> Silencio Set</span>)}</div>,
        )}</div>
      </aside>
      <section className="account-card" aria-labelledby="account-title"><p className="eyebrow">YOUR CREW STARTS HERE</p><h2 id="account-title">{title}</h2><p className="caption">{subtitle}</p>{children}</section>
    </main>
  </>
}

function useSubmission() {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const locked = useRef(false)
  const active = useRef(true)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  async function run(action: () => Promise<void>) {
    if (locked.current) return
    locked.current = true
    setPending(true)
    setError(null)
    try { await action() } catch (failure) { if (active.current) setError(failure) }
    finally { locked.current = false; if (active.current) setPending(false) }
  }
  return { pending, error, setError, run, active }
}

function Field({ id, label, children, hint }: { id: string; label: string; children: ReactNode; hint?: string }) {
  return <div className="account-field"><label htmlFor={id}>{label}</label>{children}{hint && <p className="caption" id={`${id}-hint`}>{hint}</p>}</div>
}

function checkEmail(value: string) {
  if (!value.includes('@') || value.length > 254 || /[\r\n\0]/.test(value)) throw new FormIssue('请填写有效的邮箱地址。')
}

function LoginForm({ emailHint, onRegister }: { emailHint: string; onRegister: () => void }) {
  const auth = useAuth()
  const [email, setEmail] = useState(emailHint)
  const [password, setPassword] = useState('')
  const submit = useSubmission()
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    void submit.run(async () => {
      checkEmail(email)
      if (!password || Array.from(password).length > 128) throw new FormIssue('请填写密码（不超过128字符）。')
      await auth.login(email, password)
      if (submit.active.current) setPassword('')
    })
  }
  return <form noValidate onSubmit={onSubmit} className="account-form">
    <Field id="login-email" label="邮箱"><Input id="login-email" type="email" autoComplete="email" maxLength={254} value={email} onChange={event => setEmail(event.target.value)} disabled={submit.pending} required /></Field>
    <Field id="login-password" label="密码"><Input.Password id="login-password" autoComplete="current-password" maxLength={512} value={password} onChange={event => setPassword(event.target.value)} disabled={submit.pending} required /></Field>
    <ErrorNotice error={submit.error} />
    <Button aria-label="登录" aria-busy={submit.pending || auth.busy} type="primary" htmlType="submit" block loading={submit.pending || auth.busy}>登录</Button>
    <div className="account-form-links"><span>还没有账号？</span><Button type="link" onClick={onRegister} disabled={submit.pending}>邮箱验证注册</Button></div>
    <Button type="text" onClick={() => { void auth.refresh() }} disabled={submit.pending}>重新检查登录状态</Button>
  </form>
}

function useClock() {
  const [now, setNow] = useState(Date.now)
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  return now
}

function RegisterForm({ onLogin }: { onLogin: (email: string) => void }) {
  const { api } = useAuth()
  const [step, setStep] = useState<'email' | 'verify' | 'password' | 'done'>('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [challenge, setChallenge] = useState<Verification | null>(null)
  const [cooldownUntil, setCooldownUntil] = useState(0)
  const grant = useRef<{ token: string; expiresAt: string } | null>(null)
  const submit = useSubmission()
  const now = useClock()
  const remaining = Math.max(0, Math.ceil((cooldownUntil - now) / 1000))
  useEffect(() => () => { grant.current = null }, [])

  async function requestCode() {
    checkEmail(email)
    try {
      const verification = await api.requestVerification(email)
      if (!submit.active.current) return
      setChallenge(verification); setCode(''); setStep('verify')
      setCooldownUntil(Date.now() + verification.resend_after_seconds * 1000)
    } catch (error) {
      if (submit.active.current) {
        if (error instanceof AccountError && error.code === 'RATE_LIMITED') {
          setCooldownUntil(Date.now() + (error.retryAfter ?? 60) * 1000)
        } else {
          // An uncertain resend may already have replaced the old request.
          setChallenge(null); setStep('email'); setCode('')
          if (error instanceof AccountError && error.code === 'NETWORK_UNCONFIRMED') setCooldownUntil(Date.now() + 60_000)
        }
      }
      throw error
    }
  }

  async function verifyCode() {
    if (challenge === null) throw new FormIssue('请先申请验证码。')
    if (!/^[0-9]{6}$/.test(code)) throw new FormIssue('请填写6位数字验证码。')
    try {
      const result = await api.verify(email, challenge.verification_id, code)
      if (!submit.active.current) return
      grant.current = result; setCode(''); setStep('password')
    } catch (error) {
      if (submit.active.current && error instanceof AccountError && ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE'].includes(error.code)) {
        setChallenge(null); setStep('email'); setCode('')
      }
      throw error
    }
  }

  async function finishRegistration() {
    if (grant.current === null) throw new FormIssue('请先验证邮箱。')
    if (!validNewPassword(password)) throw new FormIssue('新密码须为15—128字符，可使用空格和中文；不会自动裁剪。')
    if (password !== confirmation) throw new FormIssue('两次输入的密码不一致。')
    try {
      await api.register(grant.current.token, password)
      if (!submit.active.current) return
      grant.current = null; setPassword(''); setConfirmation(''); setChallenge(null); setStep('done')
    } catch (error) {
      if (submit.active.current && error instanceof AccountError && error.code === 'REGISTRATION_TOKEN_INVALID') {
        grant.current = null; setStep('email'); setPassword(''); setConfirmation(''); setChallenge(null)
      }
      // Preserve the same grant on an unknown register result. An explicit retry
      // can recover the committed result, rather than silently creating another request.
      throw error
    }
  }

  function resetVerification() {
    grant.current = null; setChallenge(null); setStep('email'); setCode(''); setPassword(''); setConfirmation(''); submit.setError(null)
  }

  if (step === 'done') return <div className="registration-complete" role="status"><h3>注册完成</h3><p>请使用邮箱和密码登录，首次登录后补充昵称。</p><Button type="primary" block onClick={() => onLogin(email)}>前往登录</Button></div>

  return <form noValidate className="account-form" onSubmit={event => {
    event.preventDefault()
    void submit.run(step === 'email' ? requestCode : step === 'verify' ? verifyCode : finishRegistration)
  }}>
    <p className="account-step">{step === 'email' ? '01 / 申请验证' : step === 'verify' ? '02 / 验证邮箱' : '03 / 设置密码'}</p>
    <Field id="register-email" label="注册邮箱"><Input id="register-email" type="email" autoComplete="email" maxLength={254} value={email} onChange={event => setEmail(event.target.value)} disabled={submit.pending || step !== 'email'} required /></Field>
    {step === 'verify' && <>
      <div className="account-note" role="status">验证邮件已受理，尚不能确认送达。请查看邮箱；本地开发使用测试收件箱。</div>
      <Field id="register-code" label="邮箱验证码" hint="本地倒计时仅作提示，有效性以服务端校验为准。"><Input id="register-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={event => setCode(event.target.value)} disabled={submit.pending} required /></Field>
      {challenge && <p className="caption">验证窗口剩余约 {Math.max(0, Math.ceil((Date.parse(challenge.expires_at) - now) / 1000))} 秒</p>}
    </>}
    {step === 'password' && <>
      <p className="account-note">邮箱已验证。注册不会自动登录。</p>
      <Field id="register-password" label="设置密码" hint="15—128字符，允许空格与中文。"><Input.Password id="register-password" autoComplete="new-password" maxLength={512} value={password} onChange={event => setPassword(event.target.value)} disabled={submit.pending} required /></Field>
      <Field id="register-confirm" label="再次输入密码"><Input.Password id="register-confirm" autoComplete="new-password" maxLength={512} value={confirmation} onChange={event => setConfirmation(event.target.value)} disabled={submit.pending} required /></Field>
      {grant.current && <p className="caption">注册窗口剩余约 {Math.max(0, Math.ceil((Date.parse(grant.current.expiresAt) - now) / 1000))} 秒。若先前提交结果不明，可明确重试或前往登录。</p>}
    </>}
    <ErrorNotice error={submit.error} />
    <Button aria-label={step === 'email' ? (remaining ? `${remaining}秒后可重新申请` : '申请验证码') : step === 'verify' ? '验证并设置密码' : '完成注册'} aria-busy={submit.pending}
      type="primary" htmlType="submit" block loading={submit.pending} disabled={step === 'email' && remaining > 0}>
      {step === 'email' ? (remaining ? `${remaining}秒后可重新申请` : '申请验证码') : step === 'verify' ? '验证并设置密码' : '完成注册'}
    </Button>
    {step === 'verify' && <Button block onClick={() => { void submit.run(requestCode) }} disabled={remaining > 0 || submit.pending}>{remaining ? `${remaining}秒后可重新申请` : '重新申请验证码'}</Button>}
    {step !== 'email' && <Button type="text" onClick={resetVerification} disabled={submit.pending}>重新验证或修改邮箱</Button>}
    <div className="account-form-links"><span>已有账号？</span><Button type="link" onClick={() => onLogin(email)} disabled={submit.pending}>返回登录</Button></div>
  </form>
}

export function AuthScreen({ onPreview }: { onPreview: () => void }) {
  const auth = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [emailHint, setEmailHint] = useState('')
  if (auth.state.status === 'checking') return <AccountFrame title="检查登录状态" subtitle="正在向后端确认会话，不读取剧组业务数据。"><p role="status">请稍候…</p></AccountFrame>
  return <AccountFrame title={mode === 'login' ? '登录片场' : '创建账号'} subtitle="邮箱验证注册，邮箱与密码登录。首次登录后只需补充昵称。"
    actions={<Button ghost onClick={onPreview} disabled={auth.busy}>查看布局预览</Button>}>
    {auth.state.status === 'unavailable' && <><ErrorNotice error={auth.state.error} /><p className="caption">开发环境请先启动API、配置认证并执行迁移。此处不会以演示账号代替认证。</p></>}
    {mode === 'login' ? <LoginForm key={`login-${emailHint}`} emailHint={emailHint} onRegister={() => setMode('register')} />
      : <RegisterForm onLogin={email => { setEmailHint(email); setMode('login') }} />}
  </AccountFrame>
}

export function ProfileScreen({ user, onDone }: { user: User; onDone: () => void }) {
  const auth = useAuth()
  const [draft, setDraft] = useState(user.display_name ?? '')
  const [version, setVersion] = useState(user.lock_version)
  const [conflict, setConflict] = useState(false)
  const [currentName, setCurrentName] = useState(user.display_name)
  const submit = useSubmission()
  const [notice, setNotice] = useState('')

  return <AccountFrame title={user.profile_completed ? '个人资料' : '补充你的昵称'} subtitle={user.profile_completed ? '昵称可重名，修改不会自动覆盖剧组内显示名或历史快照。' : '只需补充昵称。完成后才能进入剧组功能。'}
    actions={<Button ghost disabled={submit.pending || auth.busy} onClick={() => { void submit.run(auth.logout) }}>退出账号</Button>}>
    <p className="account-email">{user.email}</p>
    <form noValidate className="account-form" onSubmit={event => {
      event.preventDefault()
      void submit.run(async () => {
        if (!validName(draft)) throw new FormIssue('昵称去除首尾空白后须为1—30字符，不能包含控制字符。')
        try { await auth.saveProfile(trimName(draft), version); if (submit.active.current) onDone() }
        catch (error) { if (submit.active.current && error instanceof AccountError && error.code === 'VERSION_CONFLICT') setConflict(true); throw error }
      })
    }}>
      <Field id="profile-name" label="昵称" hint="1—30个Unicode字符，不需要真实姓名、学校或手机号。"><Input id="profile-name" autoComplete="nickname" maxLength={120} value={draft} onChange={event => setDraft(event.target.value)} disabled={submit.pending} required /></Field>
      <ErrorNotice error={submit.error} />
      {conflict && <div className="account-note"><p>当前版本需重新读取，输入不会被自动覆盖。</p>
        <Button onClick={() => { void submit.run(async () => {
          const fresh = await auth.reloadProfile()
          if (!submit.active.current) return
          setVersion(fresh.lock_version); setCurrentName(fresh.display_name); setConflict(false); setNotice('已读取当前版本，保留了你的输入。请确认后再次保存。')
        }) }} disabled={submit.pending}>读取当前资料版本</Button></div>}
      {notice && <p className="caption" role="status">{notice} 当前昵称：{currentName ?? '尚未填写'}</p>}
      <Button aria-label="保存昵称并继续" aria-busy={submit.pending || auth.busy} type="primary" htmlType="submit" block loading={submit.pending || auth.busy} disabled={conflict}>保存昵称并继续</Button>
      {user.profile_completed && <Button type="text" onClick={() => {
        if (trimName(draft) === user.display_name || window.confirm('尚未保存昵称修改，确认放弃并返回？')) onDone()
      }} disabled={submit.pending}>返回工作台</Button>}
    </form>
  </AccountFrame>
}
