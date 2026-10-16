import { useEffect, useState } from 'react'
import './App.css'
import { RefreshCw } from 'lucide-react'
import { api, type FinancialSummary } from './api/client'
import { Header } from './components/Header'
import { Dashboard } from './components/Dashboard'
import { Footer } from './components/Footer'
import { Login } from './components/Login'

const STORAGE_KEY = 'senus-board-user'

function App() {
  const [user, setUser] = useState<string | null>(null)
  const [summary, setSummary] = useState<FinancialSummary[]>([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) setUser(stored)
  }, [])

  useEffect(() => {
    if (!user) return
    let active = true
    api
      .summary()
      .then((data) => {
        if (!active) return
        setSummary(data)
        setSelected((prev) => prev || data[0]?.period.label || '')
        setLoading(false)
      })
      .catch((e) => {
        if (!active) return
        setError(e instanceof Error ? e.message : 'Failed to load data')
        setLoading(false)
      })
    return () => {
      active = false
    }
  }, [user])

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
        onSelect={setSelected}
      />
      <main className="app__main">
        {loading ? (
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
