const API_BASE = import.meta.env.VITE_API_URL ?? ''

const TOKEN_KEY = 'senus-board-token'
const USER_KEY = 'senus-board-user'

export interface AuthUser {
  email: string
  role: string
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getStoredUser(): AuthUser | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as AuthUser
    if (parsed && typeof parsed.email === 'string' && typeof parsed.role === 'string') return parsed
  } catch {
    return null
  }
  return null
}

export function setAuth(token: string, user: AuthUser): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export const AUTH_EXPIRED_EVENT = 'senus-auth-expired'

function authHeaders(base?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(base ?? {}) }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

function onUnauthorized(status: number): void {
  if (status === 401) {
    clearAuth()
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
  }
}

export interface Period {
  id: number
  label: string
  start_date: string
  end_date: string
  source_doc_id: number | null
}

export interface LineItem {
  line_item: string
  value_eur: number
  page?: number | null
  review_status?: string | null
  confidence?: number | null
  original_value?: number | null
}

export interface KPIValue {
  metric: string
  value: number
}

export interface PeriodMeta {
  document_title?: string | null
  currency?: string | null
  period_months?: number | null
  audited?: boolean | null
}

export interface Provenance {
  pnl?: Record<string, number>
  balance_sheet?: Record<string, number>
  cash_flow?: Record<string, number>
}

export interface PeriodDetail extends Period {
  pnl: LineItem[]
  balance_sheet: LineItem[]
  cash_flow: LineItem[]
  kpis: KPIValue[]
  metadata?: PeriodMeta | null
  provenance?: Provenance | null
}

export interface FinancialSummary {
  period: Period
  revenue: number | null
  gross_profit: number | null
  gross_margin: number | null
  operating_loss: number | null
  cash: number | null
  debt: number | null
  runway_months: number | null
}

export interface AIInsight {
  section: string
  insight: string
  confidence: number
}

export interface ReconciledItem {
  statement: string
  line_item: string
  value: number
  status: string
  confidence: number
  page: number | null
  source: string
  llm_value: number | null
  regex_value: number | null
}

export interface ReviewPayload {
  pnl: ReconciledItem[]
  balance_sheet: ReconciledItem[]
  cash_flow: ReconciledItem[]
  summary: { matched: number; flagged: number; total: number }
}

export interface DocumentSummary {
  id: number
  filename: string
  doc_type: string | null
  period_label: string | null
  period_start: string | null
  period_end: string | null
  status: string
  page_count: number | null
  error: string | null
  uploaded_at: string | null
  extracted_at: string | null
}

export interface DocumentDetail extends DocumentSummary {
  review: ReviewPayload | null
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() })
  if (!res.ok) {
    onUnauthorized(res.status)
    throw new Error(`Request failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T | null> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: authHeaders(body === undefined ? undefined : { 'Content-Type': 'application/json' }),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    onUnauthorized(res.status)
    const detail = await res.text()
    throw new Error(detail || `Request failed: ${res.status} ${res.statusText}`)
  }
  if (res.status === 204 || res.status === 205) return null as T | null
  return (await res.json()) as T
}

export interface UploadFields {
  period_label: string
  start_date: string
  end_date: string
  doc_type?: string
}

export const api = {
  login: async (email: string, password: string): Promise<{ access_token: string; role: string; email: string }> => {
    const body = new URLSearchParams({ username: email, password })
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    })
    if (!res.ok) throw new Error('Incorrect email or password')
    return (await res.json()) as { access_token: string; role: string; email: string }
  },
  me: () => get<AuthUser>('/api/auth/me'),
  periods: () => get<Period[]>('/api/periods'),
  summary: () => get<FinancialSummary[]>('/api/summary'),
  periodDetail: (label: string) => get<PeriodDetail>(`/api/periods/${label}`),
  kpis: (label: string) => get<KPIValue[]>(`/api/kpis/${label}`),
  insights: (label: string) => get<AIInsight[]>(`/api/insights/${label}`),
  documents: () => get<DocumentSummary[]>('/api/documents'),
  document: (id: number) => get<DocumentDetail>(`/api/documents/${id}`),
  uploadDocument: async (file: File, fields: UploadFields) => {
    const form = new FormData()
    form.append('file', file)
    form.append('period_label', fields.period_label)
    form.append('start_date', fields.start_date)
    form.append('end_date', fields.end_date)
    if (fields.doc_type) form.append('doc_type', fields.doc_type)
    const res = await fetch(`${API_BASE}/api/documents`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    })
    if (!res.ok) {
      onUnauthorized(res.status)
      let detail: string | null = null
      try {
        const json = await res.json()
        detail = json.detail ?? null
      } catch {
        detail = null
      }
      throw new Error(detail || (await res.text()) || 'Upload failed')
    }
    return (await res.json()) as DocumentSummary
  },
  processDocument: (id: number) => send<DocumentSummary>(`/api/documents/${id}/process`, 'POST'),
  publishDocument: (id: number) => send<DocumentSummary>(`/api/documents/${id}/publish`, 'POST'),
  deleteDocument: (id: number) => send<unknown>(`/api/documents/${id}`, 'DELETE'),
  updateDocument: (
    id: number,
    body: { period_label?: string; start_date?: string; end_date?: string; doc_type?: string },
  ) => send<DocumentSummary>(`/api/documents/${id}`, 'PATCH', body),
  updateItem: (
    label: string,
    body: { statement: string; line_item: string; action: 'confirm' | 'correct'; value?: number },
  ) => send<LineItem>(`/api/periods/${label}/items`, 'PATCH', body),
  publishPeriod: (label: string) => send<Period>(`/api/periods/${label}/publish`, 'POST'),
}

export function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0
  const n = typeof value === 'number' ? value : parseFloat(value)
  return Number.isFinite(n) ? n : 0
}

export function formatEur(value: string | number | null | undefined, digits = 0): string {
  const n = toNumber(value)
  return new Intl.NumberFormat('en-IE', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n)
}

export function formatCompactEur(value: string | number | null | undefined): string {
  const n = toNumber(value)
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `€${(n / 1_000_000).toFixed(2)}m`
  if (abs >= 1_000) return `€${(n / 1_000).toFixed(1)}k`
  return formatEur(n)
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(digits)}%`
}
