import { useEffect, useState } from 'react'
import './App.css'
import { Header } from './components/Header'
import { Footer } from './components/Footer'
import { Login } from './components/Login'
import { Dashboard } from './components/Dashboard'

const STORAGE_KEY = 'senus-board-user'

function App() {
  const [user, setUser] = useState<string | null>(null)

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) setUser(stored)
  }, [])

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
      <Header user={user} onLogout={handleLogout} />
      <main className="app__main">
        <Dashboard />
      </main>
      <Footer />
    </div>
  )
}

export default App
