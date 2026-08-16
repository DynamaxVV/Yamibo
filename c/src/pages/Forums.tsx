import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Forum, type SignInAccountStats, type SignInStats } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatBytes } from '../utils/bytes'

function SignInStatus({ status, error, onSignIn, signing }: { status: SignInAccountStats['today_status']; error: string | null; onSignIn?: () => void; signing?: boolean }) {
  const { t } = useI18n()
  if (error || status === 'unavailable') return <Badge status="error">{t('sign_in_unavailable')}</Badge>
  if (status === 'checked') return <Badge status="ok">{t('sign_in_checked')}</Badge>
  return <button className="btn-subtle sign-in-inline-action" onClick={onSignIn} disabled={signing}>{signing ? t('running') : t('sign_in_now')}</button>
}

export function Forums() {
  const { t, lang } = useI18n()
  const [forums, setForums] = useState<Forum[]>([])
  const [signInStats, setSignInStats] = useState<SignInStats | null>(null)
  const [signInStatsError, setSignInStatsError] = useState<string | null>(null)
  const [signingAccount, setSigningAccount] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  useEffect(() => {
    void api.forums().then(setForums).catch((error: Error) => setMessage(error.message))
    void api.signInStats()
      .then(data => {
        setSignInStats(data)
        setSignInStatsError(null)
      })
      .catch((error: Error) => {
        setSignInStats(null)
        setSignInStatsError(error.message)
      })
  }, [])

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

  const signIn = async (accountId: string) => {
    setSigningAccount(accountId)
    setMessage(null)
    try {
      await api.signIn(accountId)
      setSignInStats(await api.signInStats())
      setMessage(t('sign_in_completed'))
    } catch (e: any) {
      setMessage(e.message || String(e))
    } finally {
      setSigningAccount(null)
    }
  }

  return (
    <>
      <div className="threads-filter-row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{t('forum_stats')}</h2>
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
      <>
        <h2>{t('sign_in_stats')}</h2>
        {signInStatsError && <div className="threads-inline-message sign-in-stats-error">{t('sign_in_unavailable')}</div>}
        <div className="table-wrap"><table className="sign-in-table">
          <thead><tr><th>{t('account')}</th><th>{t('sign_in_recent_checkin')}</th><th>{t('sign_in_month_days')}</th><th>{t('sign_in_consecutive_days')}</th><th>{t('sign_in_total_days')}</th><th>{t('sign_in_level')}</th><th>{t('today_status')}</th></tr></thead>
          <tbody>
            {(signInStats?.accounts ?? []).map(account => <tr key={account.account_id}>
              <td className="mono sign-in-account">{account.account_id}</td>
              <td className="nowrap">{account.recent_checkin || '-'}</td>
              <td>{account.month_days ?? '-'}</td>
              <td>{account.consecutive_days ?? '-'}</td>
              <td>{account.total_days ?? '-'}</td>
              <td>{account.level || '-'}</td>
              <td><SignInStatus status={account.today_status} error={account.error} onSignIn={() => void signIn(account.account_id)} signing={signingAccount === account.account_id} /></td>
            </tr>)}
            {!signInStats && <tr><td colSpan={7}>{signInStatsError ? t('sign_in_unavailable') : t('loading')}</td></tr>}
          </tbody>
        </table></div>
      </>
    </>
  )
}
