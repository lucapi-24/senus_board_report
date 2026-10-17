import { useCallback, useEffect, useState } from 'react'
import './App.css'
import { RefreshCw } from 'lucide-react'
import {
  api,
  AUTH_EXPIRED_EVENT,
  clearAuth,
  getStoredUser,
  setAuth,
  type AuthUser,
  type FinancialSummary,
} from './api/client'
import { Header } from './components/Header'
import { Dashboard } from './components/Dashboard'
import { Ingestion } from './components/Ingestion'
import { Footer } from './components/Footer'
import { Login } from './components/Login'

type View = 'overview' | 'documents'

function App() {
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser())
  const [view, setView] = useState<View>('overview')
  const [summary, setSummary] = useState<FinancialSummary[]>([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [flaggedCount, setFlaggedCount] = useState(0)

  const loadSummary = useCallback(async () => {
    try {
      const data = await api.summary()
      setSummary(data)
      setSelected((prev) => prev || data[0]?.period.label || '')
      setError('')
      const latest = data[0]?.period.label || selected
      if (latest) {
        try {
          const detail = await api.periodDetail(latest)
          const pending = [...detail.pnl, ...detail.balance_sheet, ...detail.cash_flow].filter(
            (i) => i.review_status === 'flagged',
          ).length
          setFlaggedCount(pending)
        } catch {
          setFlaggedCount(0)
        }
      } else {
        setFlaggedCount(0)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!user) return
    let active = true
    ;(async () => {
      if (active) await loadSummary()
    })()
    return () => {
      active = false
    }
  }, [user, loadSummary])

  const handleLogin = async (email: string, password: string) => {
    const result = await api.login(email, password)
    const authUser: AuthUser = { email: result.email, role: result.role }
    setAuth(result.access_token, authUser)
    setLoading(true)
    setUser(authUser)
  }

  const handleLogout = useCallback(() => {
    clearAuth()
    setUser(null)
    setSummary([])
    setSelected('')
    setLoading(true)
  }, [])

  useEffect(() => {
    window.addEventListener(AUTH_EXPIRED_EVENT, handleLogout)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleLogout)
  }, [handleLogout])

  if (!user) {
    return <Login onLogin={handleLogin} />
  }

  return (
    <div className="app">
      <Header
        user={user.email}
        role={user.role}
        onLogout={handleLogout}
        periods={summary.map((s) => s.period)}
        selected={selected}
        onSelect={(label) => {
          setSelected(label)
          setView('overview')
        }}
        view={view}
        onNavigate={setView}
        flaggedCount={flaggedCount}
      />
      <main className="app__main">
        {view === 'documents' ? (
          <Ingestion role={user.role} onDataChanged={loadSummary} />
        ) : loading ? (
          <div className="state">
            <RefreshCw className="spin" size={28} />
            <p>Loading board report…</p>
          </div>
        ) : error ? (
          <div className="state state--error">
            <p>Could not load financial data.</p>
            <code>{error}</code>
            <p className="state__hint">Ensure the API is running at http://localhost:8000.</p>
          </div>
        ) : (
          <Dashboard summary={summary} selected={selected} onSelect={setSelected} />
        )}
      </main>
      <Footer />
    </div>
  )
}

export default App
