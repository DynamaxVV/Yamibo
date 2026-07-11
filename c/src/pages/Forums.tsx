import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Forum } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatBytes } from '../utils/bytes'

export function Forums() {
  const { t, lang } = useI18n()
  const [forums, setForums] = useState<Forum[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  useEffect(() => { api.forums().then(setForums) }, [])

  const refreshSizeCache = async () => {
    setRefreshing(true)
    setMessage(null)
    try {
      await api.refreshForumSizeCache()
      setForums(await api.forums())
      setMessage(t('forum_size_refreshed'))
    } catch (e: any) {
      setMessage(e.message || String(e))
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <>
      <div className="threads-filter-row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{t('forums')}</h2>
        <div className="threads-filter-actions">
          <button className="btn-subtle" onClick={() => void refreshSizeCache()} disabled={refreshing}>
            {refreshing ? t('running') : t('forum_size_refresh')}
          </button>
          {message && <span className="threads-inline-message">{message}</span>}
        </div>
      </div>
      <div className="table-wrap"><table>
        <thead><tr><th>{t('id')}</th><th>{t('name')}</th><th>{t('content_kind')}</th><th>{t('thread_count')}</th><th>{t('forum_data_size')}</th><th>{t('enabled')}</th><th>{t('action')}</th></tr></thead>
        <tbody>
          {forums.map(f => (
            <tr key={f.forum_id}>
              <td className="mono">{f.forum_id}</td>
              <td>{lang === 'en' ? (f.name_en || f.name) : f.name}</td>
              <td className="nowrap"><ContentBadge kind={f.content_kind} /></td>
              <td>{f.thread_count}</td>
              <td className="mono">{formatBytes(f.archive_size_bytes ?? null)}</td>
              <td><Badge status={f.enabled ? 'ok' : 'muted'}>{f.enabled ? t('yes') : t('no_label')}</Badge></td>
              <td>
                <Link to={`/threads?forum_id=${f.forum_id}`} className="btn-subtle" style={{ textDecoration: 'none' }}>
                  {t('view_threads')}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </>
  )
}
