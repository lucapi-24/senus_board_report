import { useEffect, useState } from 'react'
import { Check, FileUp } from 'lucide-react'
import {
  api,
  formatEur,
  type DocumentDetail,
  type DocumentSummary,
  type Period,
  type PeriodDetail,
  type ReconciledItem,
  type LineItem,
} from '../api/client'

const STATEMENT_LABELS: Record<string, string> = {
  pnl: 'Profit & Loss',
  balance_sheet: 'Balance Sheet',
  cash_flow: 'Cash Flow',
}

interface MergedItem extends ReconciledItem {
  review_status?: string | null
}

function merge(
  review: ReconciledItem[] = [],
  current: LineItem[] = [],
  statement: ReconciledItem['statement'],
): MergedItem[] {
  const byLabel = new Map(current.map((i) => [i.line_item, i]))
  const reviewed = new Set(review.map((item) => item.line_item))
  const merged = review.map((item) => {
    const live = byLabel.get(item.line_item)
    return {
      ...item,
      value: live?.value_eur ?? item.value,
      review_status: live?.review_status,
    }
  })
  const missingFromReview = current
    .filter((item) => !reviewed.has(item.line_item))
    .map((item) => ({
      statement,
      line_item: item.line_item,
      value: item.value_eur,
      status: item.review_status === 'reported' ? 'matched' : 'regex_only',
      confidence: item.confidence ?? 0,
      page: item.page ?? null,
      source: 'database',
      llm_value: item.original_value ?? null,
      regex_value: item.original_value != null ? item.value_eur : null,
      review_status: item.review_status,
    }))
  return [...merged, ...missingFromReview]
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString('en-IE', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function Ingestion({ role, onDataChanged }: { role?: string; onDataChanged?: () => void }) {
  const isAdmin = role === 'admin'
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [period, setPeriod] = useState<PeriodDetail | null>(null)
  const [periods, setPeriods] = useState<Period[]>([])
  const [docPeriods, setDocPeriods] = useState<Map<number, Period[]>>(new Map())
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const [file, setFile] = useState<File | null>(null)
  const [docType, setDocType] = useState('interim_results')

  const [editingDocId, setEditingDocId] = useState<number | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editStart, setEditStart] = useState('')
  const [editEnd, setEditEnd] = useState('')
  const [editDocType, setEditDocType] = useState('interim_results')

  const loadDocuments = async () => {
    const docs = await api.documents()
    setDocuments(docs)
    for (const doc of docs) {
      try {
        const docPeriods = await api.documentPeriods(doc.id)
        setDocPeriods((prev) => new Map(prev).set(doc.id, docPeriods))
      } catch {
        // Silently ignore errors for individual documents
      }
    }
    return docs
  }

  const openDocument = async (id: number, preferredPeriodLabel?: string) => {
    setError('')
    const detailData = await api.document(id)
    setDetail(detailData)
    try {
      const docPeriods = await api.documentPeriods(id)
      setPeriods(docPeriods)
      const selectedPeriod =
        docPeriods.find((candidate) => candidate.label === preferredPeriodLabel) ?? docPeriods[0]
      if (selectedPeriod) {
        const periodDetail = await api.periodDetail(selectedPeriod.label)
        setPeriod(periodDetail)
      } else {
        setPeriod(null)
      }
    } catch {
      setPeriods([])
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
      try {
        await api.processDocument(created.id)
      } catch (error) {
        await loadDocuments()
        await openDocument(created.id)
        throw error
      }
      await loadDocuments()
      await openDocument(created.id)
      onDataChanged?.()
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
      const updatedItem = await api.updateItem(period?.label ?? detail?.period_label ?? '', {
        statement: item.statement,
        line_item: item.line_item,
        action,
        value,
      })
      if (!updatedItem) throw new Error('The corrected item was not returned by the API')
      setPeriod((prev) => {
        if (!prev) return prev
        const rows = prev[item.statement]
        const exists = rows.some((row) => row.line_item === updatedItem.line_item)
        const updatedRows = exists
          ? rows.map((row) => (row.line_item === updatedItem.line_item ? updatedItem : row))
          : [...rows, updatedItem]
        return { ...prev, [item.statement]: updatedRows }
      })
      onDataChanged?.()
    })

  const handleSwitchPeriod = async (label: string) => {
    try {
      const periodDetail = await api.periodDetail(label)
      setPeriod(periodDetail)
    } catch {
      setPeriod(null)
    }
  }

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
      await loadDocuments()
      setDocPeriods((prev) => {
        const next = new Map(prev)
        next.delete(id)
        return next
      })
      if (detail?.id === id) {
        setDetail(null)
        setPeriod(null)
        setPeriods([])
      }
      onDataChanged?.()
    })
  }

  const handleEditSave = async (id: number) => {
    await run(`edit-${id}`, async () => {
      const editedDoc = documents.find((document) => document.id === id)
      const isPublished = editedDoc?.status === 'published'
      await api.updateDocument(id, {
        period_label: isPublished ? undefined : editLabel || undefined,
        start_date: isPublished ? undefined : editStart || undefined,
        end_date: isPublished ? undefined : editEnd || undefined,
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
  const review = period
    ? detail?.reviews_by_period
      ? detail.reviews_by_period[period.label] ?? null
      : detail?.review ?? null
    : null
  const hasContingentConsideration =
    detail?.reviews_by_period
      ? Object.values(detail.reviews_by_period).some((periodReview) =>
          periodReview.balance_sheet.some((item) => item.line_item === 'Contingent consideration'),
        )
      : detail?.review?.balance_sheet.some((item) => item.line_item === 'Contingent consideration') ??
        false

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
                {busy === 'upload' ? 'Uploading & processing…' : 'Upload'}
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
              {documents.map((doc) => {
                const docPeriodsList = docPeriods.get(doc.id) || []
                return (
                  <li
                    key={doc.id}
                    className={`doc-row ${detail?.id === doc.id ? 'is-selected' : ''} ${
                      editingDocId === doc.id ? 'is-editing' : ''
                    }`}
                  >
                    <button type="button" className="doc-row__main" onClick={() => openDocument(doc.id)}>
                      <strong>{doc.filename}</strong>
                      <span className="muted">
                        {docPeriodsList.length > 0
                          ? docPeriodsList.map((p) => p.label).join(', ')
                          : doc.period_label ?? '—'}
                      </span>
                    </button>
                    <div className="doc-row__periods">
                      {docPeriodsList.map((p) => (
                        <span key={p.label} className="period-badge">
                          {p.label}
                          {p.start_date && p.end_date ? ` (${formatDate(p.start_date)} – ${formatDate(p.end_date)})` : ''}
                        </span>
                      ))}
                    </div>
                    <div className="doc-row__actions">
                      {editingDocId === doc.id ? (
                        <>
                          <label className="field" style={{ margin: 0, flex: 1, minWidth: 120 }}>
                            <span>Period</span>
                            <input
                              type="text"
                              value={editLabel}
                              disabled={doc.status === 'published'}
                              onChange={(e) => setEditLabel(e.target.value)}
                              placeholder="HY2026"
                            />
                          </label>
                          <label className="field" style={{ margin: 0, flex: 1, minWidth: 110 }}>
                            <span>Start</span>
                            <input
                              type="date"
                              value={editStart}
                              disabled={doc.status === 'published'}
                              onChange={(e) => setEditStart(e.target.value)}
                            />
                          </label>
                          <label className="field" style={{ margin: 0, flex: 1, minWidth: 110 }}>
                            <span>End</span>
                            <input
                              type="date"
                              value={editEnd}
                              disabled={doc.status === 'published'}
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
                          <button type="button" className="btn btn--small" onClick={handleEditCancel}>
                            Cancel
                          </button>
                          {doc.status === 'published' && (
                            <span className="muted small">
                              Published periods are locked; only the document type can be changed.
                            </span>
                          )}
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
                          {doc.status === 'processing' && (
                            <button type="button" className="btn btn--small" disabled>
                              Processing…
                            </button>
                          )}
                          {(doc.status === 'extracted' || doc.status === 'in_review' || doc.status === 'published') && (
                            <button
                              type="button"
                              className="btn btn--small"
                              onClick={() => {
                                const firstPeriod = docPeriodsList[0]
                                if (firstPeriod) {
                                  openDocument(doc.id, firstPeriod.label)
                                } else {
                                  openDocument(doc.id)
                                }
                              }}
                            >
                              Review
                            </button>
                          )}
                          {doc.status === 'processing' && (
                            <button type="button" className="btn btn--small" disabled>
                              Processing…
                            </button>
                          )}
                          {isAdmin && (
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
                          {isAdmin && (
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
                )
              })}
            </ul>
          )}
          </section>

          {detail?.error && <div className="callout callout--error">{detail.error}</div>}

          {detail && !review?.summary && (
            <section className="panel">
              <div className="callout callout--info">
                This document has not been processed yet. Press <strong>Process</strong> to extract the data before reviewing.
              </div>
            </section>
          )}

          {detail && review?.summary && period && (
            <section className="panel review">
              <div className="review__head">
                <div className="review__title-row">
                  <div className="review__title">
                    <Check size={18} aria-hidden="true" /> Review · {period.label}
                  </div>
                  {periods.length > 1 && (
                    <div className="period-switcher">
                      <span className="period-switcher__label">Period:</span>
                      {periods.map((p) => (
                        <button
                          key={p.label}
                          type="button"
                          className={`period-btn ${period?.label === p.label ? 'is-active' : ''}`}
                          onClick={() => handleSwitchPeriod(p.label)}
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="review__summary">
                  <span className="pill pill--ok">{review.summary.matched} matched</span>
                  <span className="pill pill--warn">{pending} to review</span>
                  {isAdmin && (
                    <>
                      <button
                        type="button"
                        className="btn btn--primary btn--small"
                        disabled={busy === 'publish' || detail.status === 'published'}
                        onClick={handlePublish}
                      >
                        {detail.status === 'published' ? 'Published' : 'Publish'}
                      </button>
                      <button
                        type="button"
                        className="btn btn--small"
                        disabled={busy === 'view-document'}
                        onClick={() =>
                          run('view-document', async () => {
                            await api.openDocumentFile(detail.id)
                          })
                        }
                      >
                        {busy === 'view-document' ? 'Opening…' : 'View Document'}
                      </button>
                    </>
                  )}
                </div>
              </div>

              {(['pnl', 'balance_sheet', 'cash_flow'] as const).map((statement) => {
                const merged = merge(review![statement], period[statement], statement)
                if (
                  statement === 'balance_sheet' &&
                  hasContingentConsideration &&
                  !merged.some((item) => item.line_item === 'Contingent consideration')
                ) {
                  merged.push({
                    statement: 'balance_sheet',
                    line_item: 'Contingent consideration',
                    value: 0,
                    status: 'not_reported',
                    confidence: 0,
                    page: null,
                    source: 'not_reported',
                    llm_value: null,
                    regex_value: null,
                    review_status: null,
                  })
                }
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
                          )})}
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