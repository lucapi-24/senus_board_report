interface LogoProps {
  color?: string
  accent?: string
  height?: number
}

/**
 * Approximates the SENUS wordmark (senus.com/wp-content/uploads/2024/12/Logo.svg).
 * Uses the brand Figtree typeface with a natural-capital leaf/tick accent mark.
 */
export function Logo({ color = '#FFFFFF', accent = '#20948B', height = 32 }: LogoProps) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: height * 0.28,
        lineHeight: 1,
      }}
    >
      <svg
        width={height * 0.95}
        height={height}
        viewBox="0 0 34 36"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M17 1.5 32 9.5v17L17 34.5 2 26.5v-17L17 1.5Z"
          stroke={accent}
          strokeWidth="2.4"
          strokeLinejoin="round"
        />
        <path
          d="M11.5 20.5c3.2.4 6.1-.7 7.8-3.2 1.5-2.2 1.6-4.9.6-7.3-3.4 2.4-6.6 5.1-8.4 10.5Z"
          fill={accent}
        />
        <path
          d="M10.5 21.5c1.4-4.4 4-7.4 7.4-10"
          stroke={accent}
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
      <span
        style={{
          fontFamily: 'Figtree, sans-serif',
          fontWeight: 800,
          fontSize: height * 0.82,
          letterSpacing: '0.14em',
          color,
        }}
      >
        SENUS
      </span>
    </span>
  )
}
