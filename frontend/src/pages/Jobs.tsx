import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatDateTime } from '../utils/time'

const STATUSES = [null, 'queued', 'running', 'succeeded', 'failed', 'interrupted'] as const
const PAGE_SIZE = 50

export function Jobs() {
  const { t, lang } = useI18n()
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [confirmDelete, setConfirmDelete] = useState<JobSummary | null>(null)
  const [confirmBatchDelete, setConfirmBatchDelete] = useState(false)

  const desc = (j: JobSummary) => lang === 'en' ? j.description_en : j.description

  useEffect(() => {
    api.jobs(status || undefined).then(setJobs)
    setPage(1)
  }, [status])

  const totalPages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE))
  const paged = jobs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const handleDelete = async (j: JobSummary) => {
    setConfirmDelete(j)
  }

  const confirmDoDelete = async () => {
    if (!confirmDelete) return
    await api.deleteJob(confirmDelete.job_id)
    setConfirmDelete(null)
    api.jobs(status || undefined).then(setJobs)
  }

  const handleBatchDelete = async () => {
    if (!status) return
    await api.batchDeleteJobs(status)
    setConfirmBatchDelete(false)
    api.jobs(status || undefined).then(setJobs)
  }

  return (
    <>
      <div className="segmented">
        {STATUSES.map(s => (
          <a key={s || 'all'} className={status === s ? 'active' : ''}
            href="#" onClick={e => { e.preventDefault(); setStatus(s) }}>
            {s ? t(s) : t('all')}
          </a>
        ))}
        {status === 'succeeded' && jobs.length > 0 && (
          <button className="btn-danger-outline" style={{ marginLeft: 8, fontSize: 12, padding: '3px 8px' }}
            onClick={() => setConfirmBatchDelete(true)}>
            {t('delete_all_succeeded')}
          </button>
        )}
      </div>
      <div className="table-wrap"><table style={{ tableLayout: 'fixed', width: '100%' }}>
        <thead><tr>
          <th style={{ width: 65 }}>{t('tid')}</th>
          <th style={{ width: '35%' }}>{t('description')}</th>
          <th style={{ width: 80 }}>{t('status')}</th>
          <th style={{ width: 80 }} className="hide-mobile">{t('stage')}</th>
          <th style={{ width: 80 }} className="hide-mobile">{t('progress')}</th>
          <th style={{ width: 110 }}>{t('created_at')}</th>
          <th style={{ width: 60 }}>{t('action')}</th>
        </tr></thead>
        <tbody>
          {paged.map(j => (
            <tr key={j.job_id}>
              <td>{j.tid ? <Link to={`/threads/${j.tid}`}>{j.tid}</Link> : '-'}</td>
              <td className="truncate" title={desc(j)}><Link to={`/jobs/${j.job_id}`}>{desc(j)}</Link></td>
              <td><Badge status={j.status} /></td>
              <td className="nowrap hide-mobile">{j.stage || '-'}</td>
              <td className="nowrap hide-mobile">{j.progress_current}/{j.progress_total ?? '?'}</td>
              <td className="nowrap col-time">{formatDateTime(j.created_at)}</td>
              <td><button className="btn-subtle" onClick={() => handleDelete(j)} style={{ fontSize: 11, padding: '2px 6px' }}>{t('delete')}</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>

      {totalPages > 1 && (
        <div className="row-actions" style={{ justifyContent: 'center', gap: 4 }}>
          <button className="btn-subtle" disabled={page <= 1} onClick={() => setPage(1)}>&laquo;</button>
          <button className="btn-subtle" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>&lsaquo;</button>
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '4px 8px' }}>{page} / {totalPages}</span>
          <button className="btn-subtle" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>&rsaquo;</button>
          <button className="btn-subtle" disabled={page >= totalPages} onClick={() => setPage(totalPages)}>&raquo;</button>
        </div>
      )}

      {confirmDelete && (
        <div className="confirm-overlay" onClick={() => setConfirmDelete(null)}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t('confirm_delete')}</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {confirmDelete.status === 'running' ? t('delete_running_job_warning') : t('delete_job_confirm')}
            </p>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 16px' }}>
              {desc(confirmDelete)}
            </p>
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => setConfirmDelete(null)}>{t('cancel')}</button>
              <button className="btn-danger" onClick={confirmDoDelete}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}

      {confirmBatchDelete && (
        <div className="confirm-overlay" onClick={() => setConfirmBatchDelete(false)}>
          <div className="confirm-dialog" onClick={e => e.stopPropagation()}>
            <h2 style={{ margin: '0 0 16px', fontSize: 15, color: 'var(--text-primary)', textTransform: 'none', letterSpacing: 0 }}>{t('delete_all_succeeded')}</h2>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
              {t('batch_delete_confirm', { n: String(jobs.length) })}
            </p>
            <div className="confirm-actions">
              <button className="btn-subtle" onClick={() => setConfirmBatchDelete(false)}>{t('cancel')}</button>
              <button className="btn-danger" onClick={handleBatchDelete}>{t('confirm_execute')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
