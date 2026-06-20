import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Forum } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'

export function Forums() {
  const { t, lang } = useI18n()
  const [forums, setForums] = useState<Forum[]>([])
  useEffect(() => { api.forums().then(setForums) }, [])

  return (
    <div className="table-wrap"><table>
      <thead><tr><th>{t('id')}</th><th>{t('name')}</th><th>{t('content_kind')}</th><th>{t('thread_count')}</th><th>{t('enabled')}</th><th>{t('action')}</th></tr></thead>
      <tbody>
        {forums.map(f => (
          <tr key={f.forum_id}>
            <td className="mono">{f.forum_id}</td>
            <td>{lang === 'en' ? (f.name_en || f.name) : f.name}</td>
            <td className="nowrap"><ContentBadge kind={f.content_kind} /></td>
            <td>{f.thread_count}</td>
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
  )
}
