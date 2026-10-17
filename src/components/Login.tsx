import { useState } from 'react'
import { Lock, Mail, ArrowRight } from 'lucide-react'
import { Logo } from './Logo'

interface LoginProps {
  onLogin: (email: string, password: string) => Promise<void>
}

export function Login({ onLogin }: LoginProps) {
  const [email, setEmail] = useState('board@senus.com')
  const [password, setPassword] = useState('senus123')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.includes('@')) {
      setError('Please enter a valid email address.')
      return
    }
    if (password.length < 1) {
      setError('Please enter your password.')
      return
    }
    setError('')
    setBusy(true)
    try {
      await onLogin(email, password)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sign in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="login__panel">
        <div className="login__brand">
          <Logo color="#FFFFFF" accent="#20948B" height={38} />
        </div>
        <h1 className="login__title">Board Report</h1>
        <p className="login__subtitle">
          Sign in to access Senus PLC financial performance, AI insights and investor analytics.
        </p>

        <form className="login__form" onSubmit={submit}>
          <label className="login__field">
            <span>Email</span>
            <div className="login__input">
              <Mail size={18} />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@senus.com"
                autoComplete="email"
              />
            </div>
          </label>

          <label className="login__field">
            <span>Password</span>
            <div className="login__input">
              <Lock size={18} />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
              />
            </div>
          </label>

          {error && <div className="login__error">{error}</div>}

          <button className="btn login__submit" type="submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'} <ArrowRight size={18} />
          </button>
        </form>

        <p className="login__note">
          Demo accounts — <strong>board@senus.com</strong> (admin) or{' '}
          <strong>viewer@senus.com</strong> (read-only), password <strong>senus123</strong>.
        </p>
      </div>

      <div className="login__aside">
        <div className="login__aside-content">
          <span className="login__eyebrow">SENUS 2030 STRATEGY</span>
          <h2>Natural Capital Intelligence</h2>
          <p>
            Measuring, reporting, verifying and planning the value of natural resources — powering
            a data-driven Board view of growth, profitability, cash and returns.
          </p>
          <ul className="login__stats">
            <li>
              <strong>€837k</strong>
              <span>FY2025 revenue</span>
            </li>
            <li>
              <strong>138</strong>
              <span>Customer accounts</span>
            </li>
            <li>
              <strong>50%</strong>
              <span>Target CAGR to 2030</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  )
}
