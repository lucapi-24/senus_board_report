import { useEffect, useState } from 'react'
import { Check, FileUp } from 'lucide-react'
import {
  api,
  formatEur,
  type DocumentDetail,
  type DocumentSummary,
  type PeriodDetail,
  type ReconciledItem,
  type LineItem,
} from '../api/client'

const STATEMENT_LABELS: Record<string, string> = {
  pnl: 'Profit & Loss',
  balance_sheet: 'Balance Sheet',
  cash_flow: 'Cash Flow',
}

const STATUS_LABELS: Record<string, string> = {
  uploaded: 'Uploaded',
  processing: 'Processing',
  extracted: 'Extracted',
  in_review: 'In review',
  published: 'Published',
  error: 'Error',
}

interface MergedItem extends ReconciledItem {
  review_status?: string | null
}

function merge(review: ReconciledItem[] = [], current: LineItem[] = []): MergedItem[] {
  const byLabel = new Map(current.map((i) => [i.line_item, i]))
  return review.map((item) => {
    const live = byLabel.get(item.line_item)
    return {
      ...item,
      value: live?.value_eur ?? item.value,
      review_status: live?.review_status,
    }
  })
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge status-badge--${status}`}>{STATUS_LABELS[status] ?? status}</span>
}

export function Ingestion({ onDataChanged }: { onDataChanged?: () => void }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [period, setPeriod] = useState<PeriodDetail | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const [file, setFile] = useState<File | null>(null)
  const [periodLabel, setPeriodLabel] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [docType, setDocType] = useState('interim_results')

  const loadDocuments = async () => {
    const docs = await api.documents()
    setDocuments(docs)
    return docs
  }

  const openDocument = async (id: number) => {
    setError('')
    const detailData = await api.document(id)
    setDetail(detailData)
    if (detailData.period_label) {
      try {
        setPeriod(await api.periodDetail(detailData.period_label))
      } catch {
        setPeriod(null)
      }
    } else {
      setPeriod(null)
    }
  }

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label)
    setError('')
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setBusy('')
    }
  }

  useEffect(() => {
    let active = true
    ;(async () => {
      try {
        const docs = await api.documents()
        if (active) setDocuments(docs)
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : 'Failed to load')
      }
    })()
    return () => {
      active = false
    }
  }, [])

  const handleUpload = () =>
    run('upload', async () => {
      if (!file) throw new Error('Choose a PDF file first')
      const created = await api.uploadDocument(file, {
        period_label: periodLabel,
        start_date: startDate,
        end_date: endDate,
        doc_type: docType,
      })
      setFile(null)
      setPeriodLabel('')
      setStartDate('')
      setEndDate('')
      await loadDocuments()
      await openDocument(created.id)
    })

  const handleProcess = (id: number) =>
    run('process', async () => {
      await api.processDocument(id)
      await loadDocuments()
      await openDocument(id)
      onDataChanged?.()
    })

  const handleItem = (item: MergedItem, action: 'confirm' | 'correct') =>
    run(`${action}-${item.line_item}`, async () => {
      let value: number | undefined
      if (action === 'correct') {
        const entered = window.prompt(`Correct "${item.line_item}" (EUR)`, String(item.value))
        if (entered === null) return
        value = Number(entered.replace(/[, ]/g, ''))
        if (!Number.isFinite(value)) throw new Error('Enter a valid number')
      }
      await api.updateItem(period?.label ?? detail?.period_label ?? '', {
        statement: item.statement,
        line_item: item.line_item,
        action,
        value,
      })
      await openDocument(detail!.id)
      onDataChanged?.()
    })

  const handlePublish = () =>
    run('publish', async () => {
      const label = period?.label ?? detail?.period_label
      if (!label) throw new Error('No period to publish')
      await api.publishPeriod(label)
      if (detail) {
        await api.publishDocument(detail.id).catch(() => undefined)
        await loadDocuments()
        await openDocument(detail.id)
      }
      onDataChanged?.()
    })

  const pending = period
    ? [...period.pnl, ...period.balance_sheet, ...period.cash_flow].filter(
        (i) => i.review_status === 'flagged',
      ).length
    : 0

  return (
    <div className="container ingestion">
      <div className="ingestion__head">
        <div>
          <span className="dashboard__eyebrow">INGESTION</span>
          <h1 className="dashboard__title">Documents &amp; Review</h1>
          <p className="dashboard__lede">
            Upload a source PDF, extract it with two independent extractors, then confirm or correct
            every figure before publishing.
          </p>
        </div>
      </div>

      {error && <div className="state state--error ingest__error">{error}</div>}

      <section className="panel">
        <h2 className="panel__title">
          <FileUp size={18} aria-hidden="true" /> Upload a document
        </h2>
        <div className="upload-grid">
          <label className="field">
            <span>PDF file</span>
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label className="field">
            <span>Period label</span>
            <input
              type="text"
              placeholder="FY2026"
              value={periodLabel}
              onChange={(e) => setPeriodLabel(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Start date</span>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </label>
          <label className="field">
            <span>End date</span>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </label>
          <label className="field">
            <span>Document type</span>
            <select value={docType} onChange={(e) => setDocType(e.target.value)}>
              <option value="interim_results">Interim results</option>
              <option value="annual_report">Annual report</option>
              <option value="management_accounts">Management accounts</option>
            </select>
          </label>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy === 'upload'}
            onClick={handleUpload}
          >
            {busy === 'upload' ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </section>

      <section className="panel">
        <h2 className="panel__title">Documents</h2>
        {documents.length === 0 ? (
          <p className="muted">No documents uploaded yet.</p>
        ) : (
          <ul className="doc-list">
            {documents.map((doc) => (
              <li key={doc.id} className={`doc-row ${detail?.id === doc.id ? 'is-selected' : ''}`}>
                <button type="button" className="doc-row__main" onClick={() => openDocument(doc.id)}>
                  <strong>{doc.period_label ?? doc.filename}</strong>
                  <span className="muted">
                    {doc.filename}
                    {doc.page_count ? ` · ${doc.page_count} pages` : ''}
                  </span>
                </button>
                <StatusBadge status={doc.status} />
                <div className="doc-row__actions">
                  {(doc.status === 'uploaded' || doc.status === 'error') && (
                    <button
                      type="button"
                      className="btn btn--small"
                      disabled={busy === 'process'}
                      onClick={() => handleProcess(doc.id)}
                    >
                      Process
                    </button>
                  )}
                  {doc.status !== 'uploaded' && (
                    <button type="button" className="btn btn--small" onClick={() => openDocument(doc.id)}>
                      Review
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {detail?.error && <div className="callout callout--error">{detail.error}</div>}

      {detail && detail.review?.summary && period && (
        <section className="panel review">
          <div className="review__head">
            <h2 className="panel__title">
              <Check size={18} aria-hidden="true" /> Review · {period.label}
            </h2>
            <div className="review__summary">
              <span className="pill pill--ok">{detail.review.summary.matched} matched</span>
              <span className="pill pill--warn">{pending} to review</span>
              <button
                type="button"
                className="btn btn--primary btn--small"
                disabled={busy === 'publish' || detail.status === 'published'}
                onClick={handlePublish}
              >
                {detail.status === 'published' ? 'Published' : 'Publish'}
              </button>
            </div>
          </div>

          {(['pnl', 'balance_sheet', 'cash_flow'] as const).map((statement) => {
            const merged = merge(detail.review![statement], period[statement])
            return (
              <div key={statement} className="review__group">
                <h3 className="review__subtitle">{STATEMENT_LABELS[statement] ?? statement}</h3>
                <table className="review-table">
                  <thead>
                    <tr>
                      <th>Line item</th>
                      <th className="num">Value</th>
                      <th>Status</th>
                      <th className="num">Conf.</th>
                      <th>Page</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {merged.map((item) => {
                      const flagged = item.status !== 'matched' || item.review_status !== 'reported'
                      const other =
                        item.regex_value !== null && item.llm_value !== null
                          ? item.value === item.regex_value
                            ? item.llm_value
                            : item.regex_value
                          : null
                      return (
                        <tr
                          key={`${statement}-${item.line_item}`}
                          className={flagged && !['confirmed', 'corrected'].includes(item.review_status ?? '') ? 'is-flagged' : ''}
                        >
                          <td>{item.line_item}</td>
                          <td className="num">
                            {formatEur(item.value)}
                            {other !== null && (
                              <span className="review-table__other">
                                vs {formatEur(other)}
                              </span>
                            )}
                          </td>
                          <td>
                            <span className="muted small">
                              {item.review_status ?? item.status}
                            </span>
                          </td>
                          <td className="num small">{item.confidence.toFixed(2)}</td>
                          <td className="small muted">{item.page ?? '—'}</td>
                          <td className="review-table__actions">
                            {flagged && (
                              <>
                                <button
                                  type="button"
                                  className="btn btn--small"
                                  disabled={busy === `confirm-${item.line_item}`}
                                  onClick={() => handleItem(item, 'confirm')}
                                  title="Keep this value"
                                >
                                  Confirm
                                </button>
                                <button
                                  type="button"
                                  className="btn btn--small"
                                  disabled={busy === `correct-${item.line_item}`}
                                  onClick={() => handleItem(item, 'correct')}
                                  title="Enter a corrected value"
                                >
                                  Correct
                                </button>
                              </>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )
          })}
        </section>
      )}
    </div>
  )
}
