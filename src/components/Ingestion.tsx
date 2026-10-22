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

export function Ingestion({ role, onDataChanged }: { role?: string; onDataChanged?: () => void }) {
  const isAdmin = role === 'admin'
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [period, setPeriod] = useState<PeriodDetail | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const [file, setFile] = useState<File | null>(null)
  const [docType, setDocType] = useState('interim_results')

  // Inline edit of document metadata
  const [editingDocId, setEditingDocId] = useState<number | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editStart, setEditStart] = useState('')
  const [editEnd, setEditEnd] = useState('')
  const [editDocType, setEditDocType] = useState('interim_results')

  // Confirm detected periods for a document
  const [confirmingDocId, setConfirmingDocId] = useState<number | null>(null)
  const [confirmingPeriods, setConfirmingPeriods] = useState<string[]>([])

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
        doc_type: docType,
      })
      setFile(null)
      setDocType('interim_results')
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

  const handleDelete = async (id: number) => {
    if (!window.confirm(`Delete document ${id}? This cannot be undone.`)) return
    await run(`delete-${id}`, async () => {
      await api.deleteDocument(id)
      setDocuments(documents.filter((d) => d.id !== id))
      if (detail?.id === id) {
        setDetail(null)
        setPeriod(null)
      }
      onDataChanged?.()
    })
  }

  const handleConfirmPeriods = async (id: number) => {
    await run(`confirm-${id}`, async () => {
      await api.confirmPeriods(id, { period_labels: confirmingPeriods })
      setConfirmingDocId(null)
      setConfirmingPeriods([])
      await loadDocuments()
      await openDocument(id)
      onDataChanged?.()
    })
  }

  const handleEditSave = async (id: number) => {
    await run(`edit-${id}`, async () => {
      await api.updateDocument(id, {
        period_label: editLabel || undefined,
        start_date: editStart || undefined,
        end_date: editEnd || undefined,
        doc_type: editDocType || undefined,
      })
      setEditingDocId(null)
      await loadDocuments()
      if (detail?.id === id) {
        await openDocument(id)
      }
      onDataChanged?.()
    })
  }

  const handleEditCancel = () => {
    setEditingDocId(null)
    if (editingDocId) {
      const doc = documents.find((d) => d.id === editingDocId)
      if (doc) {
        setEditLabel(doc.period_label ?? '')
        setEditStart(doc.period_start ?? '')
        setEditEnd(doc.period_end ?? '')
        setEditDocType(doc.doc_type ?? 'interim_results')
      }
    }
  }

  const pending = period
    ? [...period.pnl, ...period.balance_sheet, ...period.cash_flow].filter(
        (i) => i.review_status === 'flagged',
      ).length
    : 0

  return (
    <div className="ingestion">
      <section className="ingestion__head">
        <div className="container">
          <div>
            <span className="dashboard__eyebrow">INGESTION</span>
            <h1 className="dashboard__title">Documents &amp; Review</h1>
            <p className="dashboard__lede">
              Upload a source PDF, extract it with two independent extractors, then confirm or correct
              every figure before publishing.
            </p>
          </div>
        </div>
      </section>
      <div className="container">

        {error && <div className="state state--error ingest__error">{error}</div>}

        {!isAdmin && (
          <div className="callout callout--readonly">
            Read-only view - your account has viewer access. Sign in as an administrator to upload,
            review or publish documents.
          </div>
        )}

        {isAdmin && (
        <section className="panel">
          <h3 className="panel__subtitle">
            <FileUp size={16} aria-hidden="true" /> Upload a document
          </h3>
          <div className="upload-grid">
          <div className="field">
            <span>PDF file</span>
            <div className="file-field">
              <input
                id="pdf-upload"
                type="file"
                accept="application/pdf"
                className="file-input-hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <label htmlFor="pdf-upload" className="btn btn--small file-trigger">
                Choose file
              </label>
            </div>
            {file && <span className="muted small">{file.name}</span>}
            {!file && <span className="muted small">No file chosen</span>}
          </div>
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
            disabled={busy === 'upload' || !file}
            onClick={handleUpload}
          >
            {busy === 'upload' ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </section>
      )}

        {/* Period confirmation panel */}
        {confirmingDocId !== null && (
          <section className="panel">
            <div className="panel__head">
              <h2>Confirm Periods</h2>
              <span className="panel__hint">
                Select which detected period(s) to process. The label is derived from the dates
                (fiscal year ends 30 June).
              </span>
            </div>
            <div className="period-confirm-list">
              {confirmingPeriods.map((label) => (
                <div key={label} className="period-confirm-item">
                  <input
                    type="checkbox"
                    checked={confirmingPeriods.includes(label)}
                    onChange={() => {
                      setConfirmingPeriods((prev) =>
                        prev.includes(label) ? prev.filter((l) => l !== label) : [...prev, label]
                      )
                    }}
                  />
                  <span>{label}</span>
                </div>
              ))}
              {confirmingPeriods.length === 0 && (
                <p className="muted small">Select at least one period to process.</p>
              )}
            </div>
            <div className="period-confirm-actions">
              <button
                type="button"
                className="btn btn--small btn--outline"
                onClick={() => {
                  setConfirmingDocId(null)
                  setConfirmingPeriods([])
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn--small btn--primary"
                disabled={confirmingPeriods.length === 0}
                onClick={() => handleConfirmPeriods(confirmingDocId!)}
              >
                Confirm & Process
              </button>
            </div>
          </section>
        )}

        <section className="panel">
          <h2 className="panel__title">Documents</h2>
          {documents.length === 0 ? (
            <p className="muted">No documents uploaded yet.</p>
          ) : (
            <ul className="doc-list">
              {documents.map((doc) => (
                <li
                  key={doc.id}
                  className={`doc-row ${detail?.id === doc.id ? 'is-selected' : ''} ${
                    editingDocId === doc.id ? 'is-editing' : ''
                  }`}
                >
                  <button type="button" className="doc-row__main" onClick={() => openDocument(doc.id)}>
                    <strong>{doc.period_label ?? doc.filename}</strong>
                    <span className="muted">
                      {doc.filename}
                      {doc.page_count ? ` · ${doc.page_count} pages` : ''}
                    </span>
                  </button>
                  <StatusBadge status={doc.status} />
                  <div className="doc-row__actions">
                    {editingDocId === doc.id ? (
                      <>
                        <label className="field" style={{ margin: 0, flex: 1, minWidth: 120 }}>
                          <span>Period</span>
                          <input
                            type="text"
                            value={editLabel}
                            onChange={(e) => setEditLabel(e.target.value)}
                            placeholder="HY2026"
                          />
                        </label>
                        <label className="field" style={{ margin: 0, flex: 1, minWidth: 110 }}>
                          <span>Start</span>
                          <input
                            type="date"
                            value={editStart}
                            onChange={(e) => setEditStart(e.target.value)}
                          />
                        </label>
                        <label className="field" style={{ margin: 0, flex: 1, minWidth: 110 }}>
                          <span>End</span>
                          <input
                            type="date"
                            value={editEnd}
                            onChange={(e) => setEditEnd(e.target.value)}
                          />
                        </label>
                        <button
                          type="button"
                          className="btn btn--small btn--primary"
                          disabled={busy.startsWith('edit-')}
                          onClick={() => handleEditSave(doc.id)}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          className="btn btn--small"
                          onClick={handleEditCancel}
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        {isAdmin && (doc.status === 'uploaded' || doc.status === 'error') && (
                          <button
                            type="button"
                            className="btn btn--small"
                            disabled={busy === 'process'}
                            onClick={() => handleProcess(doc.id)}
                          >
                            Process
                          </button>
                        )}
                        {(doc.status === 'extracted' || doc.status === 'in_review' || doc.status === 'published') && (
                          <button type="button" className="btn btn--small" onClick={() => openDocument(doc.id)}>
                            Review
                          </button>
                        )}
                        {doc.status === 'processing' && (
                          <button type="button" className="btn btn--small" disabled>
                            Processing…
                          </button>
                        )}
                        {isAdmin && doc.status !== 'published' && (
                          <button
                            type="button"
                            className="btn btn--small btn--outline"
                            onClick={() => {
                              setEditingDocId(doc.id)
                              setEditLabel(doc.period_label ?? '')
                              setEditStart(doc.period_start ?? '')
                              setEditEnd(doc.period_end ?? '')
                              setEditDocType(doc.doc_type ?? 'interim_results')
                            }}
                          >
                            Edit details
                          </button>
                        )}
                        {isAdmin && doc.status !== 'published' && (
                          <button
                            type="button"
                            className="btn btn--small btn--danger"
                            disabled={busy.startsWith('delete-')}
                            onClick={() => handleDelete(doc.id)}
                          >
                            Delete
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {detail?.error && <div className="callout callout--error">{detail.error}</div>}

        {detail && !detail.review?.summary && (
          <section className="panel">
            <div className="callout callout--info">
              This document has not been processed yet. Press <strong>Process</strong> to extract the data before reviewing.
            </div>
          </section>
        )}

        {detail && detail.review?.summary && period && (
        <section className="panel review">
          <div className="review__head">
            <h2 className="panel__title">
              <Check size={18} aria-hidden="true" /> Review · {period.label}
            </h2>
            <div className="review__summary">
              <span className="pill pill--ok">{detail.review.summary.matched} matched</span>
              <span className="pill pill--warn">{pending} to review</span>
              {isAdmin && (
                <button
                  type="button"
                  className="btn btn--primary btn--small"
                  disabled={busy === 'publish' || detail.status === 'published'}
                  onClick={handlePublish}
                >
                  {detail.status === 'published' ? 'Published' : 'Publish'}
                </button>
              )}
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
                            {isAdmin && flagged && (
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
    </div>
  )
}
