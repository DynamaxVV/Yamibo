import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Forum, type ProxyPoolHealth, type SignInAccountStats, type SignInStats } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'
import { useI18n } from '../context/I18nContext'
import { formatBytes } from '../utils/bytes'
import '../styles/catalog-lists.css'

function SignInStatus({ status, error, loading, onSignIn, signing }: { status: SignInAccountStats['today_status']; error: string | null; loading?: boolean; onSignIn?: () => void; signing?: boolean }) {
  const { t } = useI18n()
  if (loading) return <span className="threads-inline-message">{t('loading')}</span>
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
  const [proxyHealth, setProxyHealth] = useState<ProxyPoolHealth | null>(null)
  const [proxyRefreshing, setProxyRefreshing] = useState(false)
  const [signInLoadingIds, setSignInLoadingIds] = useState<Set<string>>(new Set())
  useEffect(() => {
    void api.forums().then(setForums).catch((error: Error) => setMessage(error.message))
    let signInActive = true
    void api.signInAccounts()
      .then(roster => {
        if (!signInActive) return
        const accountIds = roster.accounts.map(account => account.account_id)
        setSignInStats({ fetched_at: '', accounts: roster.accounts })
        setSignInStatsError(null)
        setSignInLoadingIds(new Set(accountIds))
        for (const accountId of accountIds) {
          void api.signInStats(accountId)
            .then(data => {
              if (!signInActive) return
              const result = data.accounts[0]
              if (!result) return
              setSignInStats(previous => previous ? {
                ...previous,
                fetched_at: data.fetched_at,
                accounts: previous.accounts.map(account => account.account_id === result.account_id ? result : account),
              } : previous)
            })
            .catch((error: Error) => {
              if (!signInActive) return
              setSignInStats(previous => previous ? {
                ...previous,
                accounts: previous.accounts.map(account => account.account_id === accountId ? { ...account, error: error.message, today_status: 'unavailable' } : account),
              } : previous)
            })
            .finally(() => {
              if (!signInActive) return
              setSignInLoadingIds(previous => {
                const next = new Set(previous)
                next.delete(accountId)
                return next
              })
            })
        }
      })
      .catch((error: Error) => {
        if (!signInActive) return
        setSignInStats(null)
        setSignInStatsError(error.message)
      })
    let proxyActive = true
    const refreshProxyHealth = () => {
      void api.proxyPoolHealth()
        .then(data => { if (proxyActive) setProxyHealth(data) })
        .catch((error: Error) => {
          if (proxyActive) setProxyHealth({ ok: false, error: error.message || t('proxy_pool_request_failed'), nodes: [] })
        })
    }
    refreshProxyHealth()
    const proxyTimer = window.setInterval(refreshProxyHealth, 30000)
    return () => {
      signInActive = false
      proxyActive = false
      window.clearInterval(proxyTimer)
    }
  }, [t])

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
      const result = await api.signInStats(accountId)
      const account = result.accounts[0]
      if (account) {
        setSignInStats(previous => previous ? {
          ...previous,
          fetched_at: result.fetched_at,
          accounts: previous.accounts.map(item => item.account_id === accountId ? account : item),
        } : previous)
      }
      setMessage(t('sign_in_completed'))
    } catch (e: any) {
      setMessage(e.message || String(e))
    } finally {
      setSigningAccount(null)
    }
  }

  const refreshProxyHealth = async () => {
    setProxyRefreshing(true)
    try {
      setProxyHealth(await api.proxyPoolHealth(true))
    } catch (e: any) {
      setProxyHealth({ ok: false, error: e.message || String(e) || t('proxy_pool_request_failed'), nodes: [] })
    } finally {
      setProxyRefreshing(false)
    }
  }

  const proxyNodes = [...(proxyHealth?.nodes ?? [])].sort((a, b) => {
    // Re-sort from the newest probe result on every render: Yamibo-reachable
    // nodes first by Yamibo latency, then blocked/dead nodes at the end.
    const aAvailability = a.yamibo_accessible ? 0 : 1
    const bAvailability = b.yamibo_accessible ? 0 : 1
    if (aAvailability !== bAvailability) return aAvailability - bAvailability
    const aDelay = a.forum_delay_ms ?? a.delay_ms
    const bDelay = b.forum_delay_ms ?? b.delay_ms
    if (aDelay == null && bDelay == null) return 0
    if (aDelay == null) return 1
    if (bDelay == null) return -1
    return aDelay - bDelay
  })

  return (
    <div className="catalog-page forum-status-page">
      <header className="catalog-heading"><h1>{lang === 'en' ? 'Forum status' : '版块与连接状态'}</h1><p>{lang === 'en' ? 'Check archive sizes, account sign-in and proxy availability.' : '查看版块归档容量、账号签到和代理连接情况。'}</p><Link to="/forum">{lang === 'en' ? 'Browse forum' : '返回论坛漫游'}</Link></header>
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
              <td data-label={t('id')} className="mono">{f.forum_id}</td>
              <td data-label={t('name')} className="table-cell-long">{lang === 'en' ? (f.name_en || f.name) : f.name}</td>
              <td data-label={t('content_kind')} className="nowrap"><ContentBadge kind={f.content_kind} /></td>
              <td data-label={t('thread_count')}>{f.thread_count}</td>
              <td data-label={t('forum_data_size')} className="mono">{formatBytes(f.archive_size_bytes ?? null)}</td>
              <td data-label={t('enabled')}><Badge status={f.enabled ? 'ok' : 'muted'}>{f.enabled ? t('yes') : t('no_label')}</Badge></td>
              <td data-label={t('action')}>
                <Link to={`/threads?forum_id=${f.forum_id}`} className="btn-subtle" style={{ textDecoration: 'none' }}>
                  {t('view_threads')}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <h2>{t('sign_in_stats')}</h2>
      {signInStatsError && <div className="threads-inline-message sign-in-stats-error">{t('sign_in_unavailable')}</div>}
      <div className="table-wrap"><table className="sign-in-table">
        <thead><tr><th>{t('account')}</th><th>{t('sign_in_recent_checkin')}</th><th>{t('sign_in_month_days')}</th><th>{t('sign_in_consecutive_days')}</th><th>{t('sign_in_total_days')}</th><th>{t('sign_in_level')}</th><th>{t('today_status')}</th></tr></thead>
        <tbody>
          {(signInStats?.accounts ?? []).map(account => <tr key={account.account_id}>
            <td data-label={t('account')} className="mono sign-in-account">{account.account_id}</td>
            <td data-label={t('sign_in_recent_checkin')} className="nowrap">{account.recent_checkin || '-'}</td>
            <td data-label={t('sign_in_month_days')}>{account.month_days ?? '-'}</td>
            <td data-label={t('sign_in_consecutive_days')}>{account.consecutive_days ?? '-'}</td>
            <td data-label={t('sign_in_total_days')}>{account.total_days ?? '-'}</td>
            <td data-label={t('sign_in_level')}>{account.level || '-'}</td>
            <td data-label={t('today_status')}><SignInStatus status={account.today_status} error={account.error} loading={signInLoadingIds.has(account.account_id)} onSignIn={() => void signIn(account.account_id)} signing={signingAccount === account.account_id} /></td>
          </tr>)}
          {!signInStats && <tr><td colSpan={7}>{signInStatsError ? t('sign_in_unavailable') : t('loading')}</td></tr>}
        </tbody>
      </table></div>

      <div className="threads-filter-row proxy-pool-heading">
        <h2 style={{ margin: 0 }}>{t('proxy_pool_nodes')}</h2>
        <div className="threads-filter-actions">
          <button className="btn-subtle" onClick={() => void refreshProxyHealth()} disabled={proxyRefreshing}>
            {proxyRefreshing ? t('running') : t('proxy_pool_test')}
          </button>
          {proxyHealth?.cached && <span className="threads-inline-message">{t('proxy_pool_cached')}</span>}
        </div>
      </div>
      {!proxyHealth ? (
        <div className="panel proxy-pool-message">{t('loading')}</div>
      ) : proxyHealth.nodes.length === 0 ? (
        <div className="panel proxy-pool-message">{proxyHealth.error || t('proxy_pool_no_nodes')}</div>
      ) : (
        <div className="table-wrap"><table className="proxy-pool-table">
          <thead><tr><th>{t('node')}</th><th>{t('proxy_pool_status')}</th><th>{t('proxy_pool_delay')}</th><th>{t('current_node')}</th></tr></thead>
          <tbody>{proxyNodes.map(node => (
            <tr key={node.name}>
              <td data-label={t('node')} className="table-cell-long mono" title={node.name}>{node.display_name || node.name}</td>
              <td data-label={t('proxy_pool_status')}><span className={`proxy-pool-status proxy-pool-status-${node.status}`}>{t(`proxy_pool_status_${node.status}`)}</span></td>
              <td data-label={t('proxy_pool_delay')}>{node.delay_ms == null ? '-' : `${node.delay_ms} ms`}</td>
              <td data-label={t('current_node')}>{proxyHealth.selector_group?.current_node === node.name ? '✓' : ''}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  )
}
