import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Folder } from 'lucide-react'
import { Logo } from './Logo'

interface HeaderPeriod {
  label: string
  start_date: string
  end_date: string
}

interface HeaderProps {
  user?: string
  role?: string
  onLogout?: () => void
  periods?: HeaderPeriod[]
  selected?: string
  onSelect?: (label: string) => void
  view?: 'company' | 'overview' | 'documents'
  onNavigate?: (view: 'company' | 'overview' | 'documents') => void
  flaggedCount?: number
}

function DocumentPicker({
  periods,
  selected,
  onSelect,
}: {
  periods: HeaderPeriod[]
  selected?: string
  onSelect?: (label: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="doc-picker" ref={ref}>
      <button
        type="button"
        className="doc-picker__btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Folder size={18} aria-hidden="true" />
        <span className="doc-picker__labels">
          <span className="doc-picker__kicker">Document</span>
          <strong className="doc-picker__current">{selected || 'Select'}</strong>
        </span>
        <ChevronDown size={16} className={`doc-picker__chevron ${open ? 'is-open' : ''}`} aria-hidden="true" />
      </button>

      {open && (
        <ul className="doc-picker__menu" role="listbox" aria-label="Switch document">
          {periods.map((p) => {
            const active = p.label === selected
            return (
              <li key={p.label}>
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={`doc-picker__item ${active ? 'is-active' : ''}`}
                  onClick={() => {
                    onSelect?.(p.label)
                    setOpen(false)
                  }}
                >
                  <span className="doc-picker__item-main">
                    <strong>{p.label}</strong>
                    <span>
                      {p.start_date} → {p.end_date}
                    </span>
                  </span>
                  {active && <Check size={16} aria-hidden="true" />}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export function Header({
  user,
  role,
  onLogout,
  periods = [],
  selected,
  onSelect,
  view = 'company',
  onNavigate,
  flaggedCount,
}: HeaderProps) {
  return (
    <header className="senus-header">
      <div className="senus-header__inner">
        <a className="senus-header__brand" href="/" aria-label="Senus Board Report home">
          <Logo color="#FFFFFF" accent="#20948B" height={32} />
          <span className="senus-header__product">Board Report</span>
        </a>

        <DocumentPicker periods={periods} selected={selected} onSelect={onSelect} />

        <nav className="senus-header__nav" aria-label="Main">
          <button
            type="button"
            className={`senus-header__link ${view === 'company' ? 'senus-header__link--active' : ''}`}
            onClick={() => onNavigate?.('company')}
          >
            Company
          </button>
          <button
            type="button"
            className={`senus-header__link ${view === 'overview' ? 'senus-header__link--active' : ''}`}
            onClick={() => onNavigate?.('overview')}
          >
            Financials
          </button>
          <button
            type="button"
            className={`senus-header__link ${view === 'documents' ? 'senus-header__link--active' : ''}`}
            onClick={() => onNavigate?.('documents')}
          >
            Documents
            {flaggedCount !== undefined && flaggedCount > 0 && (
              <span className="nav-badge">{flaggedCount}</span>
            )}
          </button>
        </nav>

        <div className="senus-header__actions">
          {user && <span className="senus-header__user">{user}</span>}
          {role && <span className={`role-badge role-badge--${role}`}>{role}</span>}
          <button type="button" className="btn senus-header__login" onClick={onLogout}>
            {user ? 'Sign out' : 'Login'}
          </button>
        </div>
      </div>
    </header>
  )
}
