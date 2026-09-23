import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SeriesSummary } from '../api/client'
import { Badge } from '../components/Badge'
import { PaginationControls } from '../components/PaginationControls'
import { useI18n } from '../context/I18nContext'
import '../styles/catalog-lists.css'

export function Series() {
  const { t, lang } = useI18n()
  const [allSeries, setAllSeries] = useState<SeriesSummary[]>([])
  const [q, setQ] = useState(() => sessionStorage.getItem('series_q') || '')
  const [reviewFilter, setReviewFilter] = useState<'all' | 'review' | 'confirmed'>(() => (sessionStorage.getItem('series_reviewFilter') as 'all' | 'review' | 'confirmed') || 'all')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const PAGE_SIZE = 50

  useEffect(() => { api.series().then(setAllSeries).catch(e => setError(e.message)).finally(() => setLoading(false)) }, [])

  useEffect(() => {
    sessionStorage.setItem('series_q', q)
    sessionStorage.setItem('series_reviewFilter', reviewFilter)
  }, [q, reviewFilter])

  const filtered = allSeries.filter(s => {
    if (reviewFilter === 'review' && !s.needs_review) return false
    if (reviewFilter === 'confirmed' && s.needs_review) return false
    if (q.trim()) {
      const keywords = q.trim().toLowerCase().split(/\s+/).filter(Boolean)
      const haystack = [s.canonical_title, s.series_key, s.author_guess].filter(Boolean).join(' ').toLowerCase()
      if (!keywords.every(kw => haystack.includes(kw))) return false
    }
    return true
  })

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div className="catalog-page">
      <header className="catalog-heading"><h1>{lang === 'en' ? 'Series' : '作品系列'}</h1><p>{lang === 'en' ? 'Browse collected works and review their grouping.' : '按作品浏览归档内容，检查系列归属与待复核项目。'}</p></header>
      <div className="filter-bar">
        <input
          className="filter-search"
          aria-label={t('search_series_placeholder')}
          value={q}
          onChange={e => { setQ(e.target.value); setPage(1) }}
          placeholder={t('search_series_placeholder')}
        />
        <div className="filter-group">
          <select aria-label={t('review')} value={reviewFilter} onChange={e => { setReviewFilter(e.target.value as typeof reviewFilter); setPage(1) }}>
            <option value="all">{t('all')}</option>
            <option value="review">{t('pending_review')}</option>
            <option value="confirmed">{t('confirmed')}</option>
          </select>
        </div>
      </div>

      <div id="series-pagination-top" />
      <p className="catalog-result-count">{lang === 'en' ? `${filtered.length} series` : `共 ${filtered.length} 个系列`}</p>
      {error ? <div className="panel" role="alert">{error}</div> : loading ? <div className="panel" role="status">{t('loading')}</div> : <div className="catalog-list">
        {paged.length === 0 ? <div className="panel">{t('no_match')}</div> : paged.map(s => {
          const title = s.canonical_title || (lang === 'en' ? 'Untitled series' : '未命名系列')
          return <article className="catalog-row" key={s.series_id}>
            <div className="catalog-main">
              <Link className="catalog-title" to={`/series/${s.series_id}`}>{title}</Link>
              <details className="catalog-title-details"><summary>{lang === 'en' ? 'Full title and series key' : '完整标题与系列键'}</summary><p>{title}</p><p className="mono">{s.series_key || '—'}</p></details>
              <div className="catalog-meta"><span className="mono">#{s.series_id}</span><span>{t('author')}: {s.author_guess || '—'}</span><span>{t('thread_count')}: {s.thread_count}</span></div>
            </div>
            <div className="catalog-row-actions"><Badge status={s.needs_review ? 'warn' : 'ok'}>{s.needs_review ? t('needs_review') : t('confirmed')}</Badge><Link className="btn-subtle" to={`/series/${s.series_id}`}>{lang === 'en' ? 'View series' : '查看系列'}</Link></div>
          </article>
        })}
      </div>}

      <PaginationControls page={page} totalPages={totalPages} onPageChange={setPage} scrollTargetId="series-pagination-top" />
    </div>
  )
}
