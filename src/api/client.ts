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
  origin?: string | null
  review_status?: string | null
  confidence?: number | null
  confidence_factors?: Record<string, boolean | null> | null
  original_value?: number | null
}

export type FinancialStatement = 'pnl' | 'balance_sheet' | 'cash_flow'

export interface KPIValue {
  metric: string
  value: number | null
  formula?: string | null
  kind: 'reported' | 'derived' | 'na'
  note?: string | null
}

export interface PeriodMeta {
  document_title?: string | null
  currency?: string | null
  period_months?: number | null
  audited?: boolean | null
  source_description?: string | null
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
  duration_months: number
  revenue: number | null
  revenue_origin: string
  gross_profit: number | null
  gross_profit_origin: string
  gross_margin: number | null
  gross_margin_origin: string
  operating_loss: number | null
  operating_loss_origin: string
  operating_margin: number | null
  operating_margin_origin: string
  cash: number | null
  cash_origin: string
  debt: number | null
  debt_origin: string
  runway_months: number | null
  runway_origin: string
  revenue_yoy_change_pct: number | null
  revenue_yoy_period_label: string | null
}

export interface AIInsight {
  section: string
  insight: string
  data: string | null
  interpretation: string | null
  confidence: number
  verified_claims: number
  total_claims: number
  sources: InsightSource[]
  evidence: InsightEvidence[]
}

export interface InsightSource {
  source_id: string
  claim_text: string
  label: string
  value: number
  origin: string
  period_label?: string | null
  formula: string | null
  pages: number[]
  pdf_excerpts: InsightEvidence[]
  source_note: string | null
  verified: boolean
}

export interface InsightEvidence {
  page: number
  quote: string
  period_label?: string | null
}

export interface PeriodCompareResult {
  label_a: string
  label_b: string
  insight: string
  confidence: number
  data: string | null
  interpretation: string | null
  verified_claims: number
  total_claims: number
  sources: InsightSource[]
  evidence: InsightEvidence[]
}

export interface ReconciledItem {
  statement: FinancialStatement
  line_item: string
  value: number
  status: string
  confidence: number
  confidence_factors?: Record<string, boolean | null>
  page: number | null
  origin?: string | null
  source: string
  llm_value: number | null
  regex_value: number | null
}

export interface ReviewPayload {
  pnl: ReconciledItem[]
  balance_sheet: ReconciledItem[]
  cash_flow: ReconciledItem[]
  summary: { matched: number; flagged: number; total: number }
  note_warnings?: NoteWarning[]
  llm_trace?: LLMCallTrace
}

export interface LLMCallTrace {
  call_id?: string
  document_id?: number | null
  period_label?: string
  model?: string
  status?: string
  api_result?: string
  error_code?: string | null
  error_message?: string | null
  warning?: string | null
  completed_at?: string
  latency_ms?: number | null
  rate_limit_wait_ms?: number | null
  attempts?: number
  attempts_detail?: Array<Record<string, unknown>>
  prompt_tokens?: number | null
  completion_tokens?: number | null
  total_tokens?: number | null
  estimated_input_tokens?: number | null
  max_tokens?: number | null
  estimated_request_tokens?: number | null
  expected_count?: number
  grounded_count?: number
  raw_item_count?: number
  normalized_item_count?: number
  accepted_count?: number
  discarded_count?: number
  statement_pages?: number[]
  missing_expected?: Record<string, string[]>
  returned_lines?: Array<{
    statement?: string
    line_item?: string
    value?: number
    page?: number
  }>
  discarded_lines?: Array<Record<string, unknown>>
  raw_response?: string
}

export interface NoteWarning {
  statement: FinancialStatement
  line_item: string
  table_value: number
  note_value: number
  page: number
  quote: string
  message: string
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
  detected_periods?: DetectedPeriod[]
  periods_confirmed?: boolean
  period_validation_errors?: string[]
  periods_manually_edited?: boolean
  edited_by?: string | null
  edited_at?: string | null
}

export interface DocumentDetail extends DocumentSummary {
  review: ReviewPayload | null
  reviews_by_period?: Record<string, ReviewPayload> | null
  llm_calls?: LLMCallTrace[]
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

export class PeriodReplacementError extends Error {
  readonly periods: string[]

  constructor(periods: string[], message: string) {
    super(message)
    this.name = 'PeriodReplacementError'
    this.periods = periods
  }
}

function isReplacementDetail(
  value: unknown,
): value is { code: string; periods: string[]; message: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    'code' in value &&
    value.code === 'period_replacement_confirmation_required' &&
    'periods' in value &&
    Array.isArray(value.periods) &&
    value.periods.every((period) => typeof period === 'string') &&
    'message' in value &&
    typeof value.message === 'string'
  )
}

export interface UploadFields {
  doc_type?: string
}

export interface DetectedPeriod {
  label: string | null
  start_date: string | null
  end_date: string | null
  column?: number
  pages?: number[]
  source?: string
  score?: number
  is_primary?: boolean
  duration_months?: number | null
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
  insights: (label: string, force = false) =>
    get<AIInsight[]>(`/api/insights/${label}${force ? '?force=true' : ''}`),
  comparePeriods: async (body: {
    period_label_a: string
    period_label_b: string
  }): Promise<PeriodCompareResult> => {
    const res = await fetch(`${API_BASE}/api/insights/compare`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      onUnauthorized(res.status)
      throw new Error(await res.text() || 'Compare failed')
    }
    return (await res.json()) as PeriodCompareResult
  },
  documents: () => get<DocumentSummary[]>('/api/documents'),
  document: (id: number) => get<DocumentDetail>(`/api/documents/${id}`),
  documentPeriods: (id: number) => get<Period[]>(`/api/documents/${id}/periods`),
  openDocumentFile: async (id: number, page?: number) => {
    const viewer = window.open('about:blank', '_blank')
    if (!viewer) throw new Error('Allow pop-ups to view the source document')
    viewer.opener = null

    try {
      const res = await fetch(`${API_BASE}/api/documents/${id}/file`, {
        headers: authHeaders(),
      })
      if (!res.ok) {
        onUnauthorized(res.status)
        throw new Error(`Could not open document: ${res.status} ${res.statusText}`)
      }
      const objectUrl = URL.createObjectURL(await res.blob())
      viewer.location.href = page ? `${objectUrl}#page=${page}` : objectUrl
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10 * 60 * 1000)
    } catch (error) {
      viewer.close()
      throw error
    }
  },
  uploadDocument: async (file: File, fields: UploadFields) => {
    const form = new FormData()
    form.append('file', file)
    if (fields.doc_type) form.append('doc_type', fields.doc_type)
    const res = await fetch(`${API_BASE}/api/documents`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    })
    if (!res.ok) {
      onUnauthorized(res.status)
      let detail: string | null
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
  confirmDocumentPeriods: (
    id: number,
    body: { periods: Array<{ column: number; start_date: string; end_date: string }> },
  ) => send<DocumentSummary>(`/api/documents/${id}/confirm-periods`, 'POST', body),
  processDocument: async (id: number, replaceExisting = false): Promise<DocumentSummary> => {
    const query = replaceExisting ? '?replace_existing=true' : ''
    const res = await fetch(`${API_BASE}/api/documents/${id}/process${query}`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!res.ok) {
      onUnauthorized(res.status)
      const raw = await res.text()
      let payload: unknown
      try {
        payload = JSON.parse(raw)
      } catch {
        payload = null
      }
      const detail =
        typeof payload === 'object' && payload !== null && 'detail' in payload
          ? payload.detail
          : null
      if (res.status === 409 && isReplacementDetail(detail)) {
        throw new PeriodReplacementError(detail.periods, detail.message)
      }
      const message =
        typeof detail === 'string'
          ? detail
          : typeof detail === 'object' && detail !== null && 'message' in detail &&
              typeof detail.message === 'string'
            ? detail.message
            : raw || `Request failed: ${res.status} ${res.statusText}`
      throw new Error(message)
    }
    return (await res.json()) as DocumentSummary
  },
  publishDocument: (id: number) => send<DocumentSummary>(`/api/documents/${id}/publish`, 'POST'),
  deleteDocument: (id: number) => send<unknown>(`/api/documents/${id}`, 'DELETE'),
  updateDocument: (
    id: number,
    body: { doc_type?: string },
  ) => send<DocumentSummary>(`/api/documents/${id}`, 'PATCH', body),
  updateItem: (
    label: string,
    body: {
      statement: FinancialStatement
      line_item: string
      action: 'confirm' | 'correct'
      value?: number
    },
  ) => send<LineItem>(`/api/periods/${label}/items`, 'PATCH', body),
  publishPeriod: (label: string) => send<Period>(`/api/periods/${label}/publish`, 'POST'),
}

export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null
  const n = typeof value === 'number' ? value : parseFloat(value)
  return Number.isFinite(n) ? n : null
}

export function formatEur(value: string | number | null | undefined, digits = 0): string {
  const n = toNumber(value)
  if (n === null) return 'n/d'
  return new Intl.NumberFormat('en-IE', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n)
}

export function formatCompactEur(value: string | number | null | undefined): string {
  const n = toNumber(value)
  if (n === null) return 'n/d'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `€${(n / 1_000_000).toFixed(2)}m`
  if (abs >= 1_000) return `€${(n / 1_000).toFixed(1)}k`
  return formatEur(n)
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'n/d'
  return `${value.toFixed(digits)}%`
}
