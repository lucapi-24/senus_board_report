import { Logo } from './Logo'

function LinkedInIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M4.98 3.5C4.98 4.88 3.87 6 2.5 6S0 4.88 0 3.5 1.12 1 2.5 1 4.98 2.12 4.98 3.5zM.5 8h4V24h-4V8zm7.5 0h3.8v2.2h.05c.53-1 1.83-2.2 3.76-2.2 4.02 0 4.76 2.65 4.76 6.1V24h-4v-7.1c0-1.7-.03-3.9-2.37-3.9-2.38 0-2.74 1.86-2.74 3.78V24h-4V8z" />
    </svg>
  )
}

function XIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M18.9 2H22l-7.4 8.5L23.3 22h-6.8l-5.3-7-6.1 7H2l7.9-9.1L1.1 2h7l4.8 6.4L18.9 2zm-1.2 18h1.9L7.4 4H5.4l12.3 16z" />
    </svg>
  )
}

function YouTubeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.5 12 3.5 12 3.5s-7.5 0-9.4.6A3 3 0 0 0 .5 6.2 31 31 0 0 0 0 12a31 31 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.6 9.4.6 9.4.6s7.5 0 9.4-.6a3 3 0 0 0 2.1-2.1A31 31 0 0 0 24 12a31 31 0 0 0-.5-5.8zM9.5 15.5v-7l6.3 3.5-6.3 3.5z" />
    </svg>
  )
}

function FacebookIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M24 12a12 12 0 1 0-13.9 11.9v-8.4H7.1V12h3V9.4c0-3 1.8-4.6 4.5-4.6 1.3 0 2.6.2 2.6.2v2.9h-1.5c-1.4 0-1.9.9-1.9 1.8V12h3.3l-.5 3.5h-2.8v8.4A12 12 0 0 0 24 12z" />
    </svg>
  )
}

export function Footer() {
  return (
    <footer className="senus-footer">
      <div className="container senus-footer__inner">
        <div className="senus-footer__brand">
          <Logo color="#023C28" accent="#20948B" height={30} />
          <p className="senus-footer__tagline">
            Technologies to Measure Nature. Transforming complex environmental data into actionable
            insights.
          </p>
        </div>

        <div className="senus-footer__cols">
          <div>
            <h4>Platform</h4>
            <ul>
              <li>Overview</li>
              <li>Growth &amp; Revenue</li>
              <li>Profitability</li>
              <li>Cash &amp; Liquidity</li>
            </ul>
          </div>
          <div>
            <h4>Company</h4>
            <ul>
              <li>Investor Relations</li>
              <li>Euronext Access Dublin</li>
              <li>SENUS 2030 Strategy</li>
            </ul>
          </div>
          <div>
            <h4>Connect</h4>
            <div className="senus-footer__social">
              <a href="https://linkedin.com/company/senustech" aria-label="LinkedIn" target="_blank" rel="noreferrer">
                <LinkedInIcon />
              </a>
              <a href="https://twitter.com/SenusTech" aria-label="X" target="_blank" rel="noreferrer">
                <XIcon />
              </a>
              <a href="https://youtube.com/@SenusTech" aria-label="YouTube" target="_blank" rel="noreferrer">
                <YouTubeIcon />
              </a>
              <a href="https://facebook.com/SenusTech" aria-label="Facebook" target="_blank" rel="noreferrer">
                <FacebookIcon />
              </a>
            </div>
          </div>
        </div>
      </div>
      <div className="container senus-footer__bottom">
        <span>© Copyright 2026 Senus PLC. All Rights Reserved.</span>
        <span className="senus-footer__legal">Privacy · Cookie Policy · User Agreement</span>
      </div>
    </footer>
  )
}
