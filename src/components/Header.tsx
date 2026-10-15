import { Logo } from './Logo'

interface HeaderProps {
  user?: string
  onLogout?: () => void
}

export function Header({ user, onLogout }: HeaderProps) {
  return (
    <header className="senus-header">
      <div className="senus-header__inner">
        <a className="senus-header__brand" href="/" aria-label="Senus Board Report home">
          <Logo color="#FFFFFF" accent="#20948B" height={32} />
          <span className="senus-header__product">Board Report</span>
        </a>

        <nav className="senus-header__nav" aria-label="Main">
          <span className="senus-header__link senus-header__link--active">Overview</span>
          <span className="senus-header__link">Investors</span>
          <span className="senus-header__link">Documents</span>
        </nav>

        <div className="senus-header__actions">
          {user && <span className="senus-header__user">{user}</span>}
          <button className="btn senus-header__login" onClick={onLogout}>
            {user ? 'Sign out' : 'Login'}
          </button>
        </div>
      </div>
    </header>
  )
}
