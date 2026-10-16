import { useCallback, useEffect, useState } from 'react'
import './App.css'
import { RefreshCw } from 'lucide-react'
import { api, type FinancialSummary } from './api/client'
import { Header } from './components/Header'
import { Dashboard } from './components/Dashboard'
import { Ingestion } from './components/Ingestion'
import { Footer } from './components/Footer'
import { Login } from './components/Login'

const STORAGE_KEY = 'senus-board-user'

type View = 'overview' | 'documents'

function App() {
  const [user, setUser] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY))
  const [view, setView] = useState<View>('overview')
  const [summary, setSummary] = useState<FinancialSummary[]>([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadSummary = useCallback(async () => {
    try {
      const data = await api.summary()
      setSummary(data)
      setSelected((prev) => prev || data[0]?.period.label || '')
      setError('')
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

  const handleLogin = (email: string) => {
    localStorage.setItem(STORAGE_KEY, email)
    setUser(email)
  }

  const handleLogout = () => {
    localStorage.removeItem(STORAGE_KEY)
    setUser(null)
  }

  if (!user) {
    return <Login onLogin={handleLogin} />
  }

  return (
    <div className="app">
      <Header
        user={user}
        onLogout={handleLogout}
        periods={summary.map((s) => s.period)}
        selected={selected}
        onSelect={(label) => {
          setSelected(label)
          setView('overview')
        }}
        view={view}
        onNavigate={setView}
      />
      <main className="app__main">
        {view === 'documents' ? (
          <Ingestion onDataChanged={loadSummary} />
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
