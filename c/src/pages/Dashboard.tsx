import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, BookOpen, Download, Library, ListTodo } from 'lucide-react'
import { api, type DashboardData, type ThreadSummary } from '../api/client'
import { LoadingIndicator } from '../components/LoadingIndicator'
import packageInfo from '../../package.json'
import { useI18n } from '../context/I18nContext'
import { formatThreadListTitle } from '../utils/threadTitle'
import { formatDateTime } from '../utils/time'
import '../styles/dashboard-editorial.css'

function formatDateTimeStacked(value: string | null) {
  const formatted = formatDateTime(value)
  if (formatted === '-') return <span className="archive-home__datetime">—</span>
  const [datePart, timePart, ...rest] = formatted.split(' ')
  if (!datePart || !timePart || rest.length > 0) return <span className="archive-home__datetime">{formatted}</span>
  return <span className="archive-home__datetime"><span>{datePart}</span><span>{timePart}</span></span>
}

export function Dashboard() {
  const { lang, tx } = useI18n()
  const currentDate = new Intl.DateTimeFormat(lang === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric', month: 'short', day: '2-digit',
  }).format(new Date())
  const [data, setData] = useState<DashboardData | null>(null)
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [forumNames, setForumNames] = useState<Record<number, string>>({})
  const [canonicalForumNames, setCanonicalForumNames] = useState<Record<number, string>>({})
  const [forum, setForum] = useState('all')
  const [error, setError] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [sampleLoading, setSampleLoading] = useState(false)
  const filterRequestRef = useRef(0)
  const forumSamplesRef = useRef(new Map<number, ThreadSummary[]>())

  useEffect(() => {
    api.forums().then(items => {
      setForumNames(Object.fromEntries(items.map(item => [item.forum_id, lang === 'en' ? item.name_en || item.name : item.name])))
      setCanonicalForumNames(Object.fromEntries(items.map(item => [item.forum_id, item.name.trim()])))
    }).catch(() => {})
  }, [lang])

  useEffect(() => {
    let active = true
    api.dashboard(10)
      .then(summary => {
        if (!active) return
        setData(summary)
        setThreads(summary.recent_threads)
        forumSamplesRef.current.clear()
        setError(false)
      })
      .catch(() => { if (active) setError(true) })
      .finally(() => { if (active) setIsLoading(false) })
    return () => { active = false }
  }, [])

  const filtered = useMemo(() => forum === 'all' ? threads : threads.filter(item => item.forum_id === Number(forum)), [threads, forum])
  const selectForum = async (nextForum: string) => {
    setForum(nextForum)
    setError(false)
    const requestId = ++filterRequestRef.current
    if (nextForum === 'all') {
      setSampleLoading(false)
      setThreads(data?.recent_threads ?? [])
      return
    }
    const forumId = Number(nextForum)
    const cached = forumSamplesRef.current.get(forumId)
    if (cached) {
      setSampleLoading(false)
      setThreads(cached)
      return
    }
    setSampleLoading(true)
    try {
      const result = await api.threads({ forum_id: forumId, sort_key: 'sync_time', sort_dir: 'desc', page_size: 10 })
      if (filterRequestRef.current !== requestId) return
      forumSamplesRef.current.set(forumId, result.items)
      setThreads(result.items)
    } catch {
      if (filterRequestRef.current === requestId) setError(true)
    } finally {
      if (filterRequestRef.current === requestId) setSampleLoading(false)
    }
  }
  const stats = [
    { label: tx('归档帖子', 'Archived threads'), value: data?.thread_count, to: '/threads', icon: BookOpen },
    { label: tx('作品系列', 'Series'), value: data?.series_count, to: '/series', icon: Library },
    { label: tx('导出档案', 'Exports'), value: data?.export_count, to: '/exports', icon: Download },
  ]
  const primaryForumNames = new Set(['海域区', '动漫区', '漫画区', '文学区', '管理版', '轻小说区'])
  const forumCounts = Object.entries(data?.forum_counts ?? {})
    .map(([id, count]) => ({ id: Number(id), count, name: canonicalForumNames[Number(id)] }))
    .filter(({ count }) => count > 0)
  const namedForumCounts = forumCounts
    .filter(({ name }) => name && primaryForumNames.has(name))
    .map(({ id, count }) => ({ id, count, other: false }))
  const otherForumCount = forumCounts
    .filter(({ name }) => !name || !primaryForumNames.has(name))
    .reduce((total, { count }) => total + count, 0)
  const groupedForumCounts = [
    ...namedForumCounts,
    ...(otherForumCount > 0 ? [{ id: null, count: otherForumCount, other: true }] : []),
  ].sort((a, b) => b.count - a.count)

  return <div className="archive-home">
    <header className="archive-home__masthead">
      <div><h1>{lang === 'en' ? 'Archive console' : <><span>归档</span>控制台</>}</h1></div>
      <div className="archive-home__meta" aria-label={tx('归档控制台信息', 'Archive console details')}>
        <div><span>{tx('今天', 'Today')}</span><time>{currentDate}</time></div>
        <div><span>{tx('当前版本', 'Version')}</span><strong>v{packageInfo.version}</strong></div>
      </div>
    </header>
    {error && <div role="alert" className="archive-home__error">{tx('数据加载失败，请刷新页面后重试。', 'Unable to load dashboard data. Refresh the page and try again.')}</div>}
    <div className="archive-home__stats">
      {stats.map(({ label, value, to, icon: Icon }) => <Link key={label} to={to} className="archive-home__stat">
        <Icon aria-hidden="true" /><span><strong>{value == null ? '—' : value.toLocaleString()}</strong><small>{label}</small></span>
      </Link>)}
      <Link to="/jobs" className="archive-home__stat archive-home__stat--jobs">
        <ListTodo aria-hidden="true" /><span><strong>{data?.job_control.running == null ? '—' : data.job_control.running.toLocaleString()}</strong><small>{data ? `${data.job_control.jobs_enabled ? tx('运行任务', 'Running tasks') : tx('队列已暂停', 'Queue paused')} · ${data.job_control.queued} ${tx('个排队中', 'queued')}` : tx('运行任务', 'Running tasks')}</small></span>
      </Link>
    </div>
    {forumCounts.length > 0 && <nav className="archive-home__forum-counts" aria-label={tx('各分区归档帖子数', 'Archived threads by forum')}>
      <strong>{tx('分区收录', 'By forum')}</strong>
      {groupedForumCounts.map(({ id, count, other }) => other ? <span key="other" title={tx('其他分区的归档帖子总数', 'Total archived threads in other forums')}>
        <span>{tx('其他', 'Other')}</span><b>{count.toLocaleString()}</b>
      </span> : <Link key={id} to={`/threads?forum_id=${id}`} title={tx(`查看${forumNames[id!] || `版块 #${id}`}的归档帖子`, `View archived threads in ${forumNames[id!] || `forum #${id}`}`)}>
        <span>{forumNames[id!] || tx(`版块 #${id}`, `Forum #${id}`)}</span><b>{count.toLocaleString()}</b>
      </Link>)}
    </nav>}
    {data && (data.job_control.interrupted > 0 || data.job_control.paused > 0) && <Link to="/jobs" className="archive-home__notice"><strong>{tx('待处理任务', 'Tasks needing attention')}</strong><span>{data.job_control.interrupted} {tx('个中断', 'interrupted')}，{data.job_control.paused} {tx('个暂停', 'paused')}</span><ArrowRight aria-hidden="true" /></Link>}
    <section className="archive-home__recent" aria-labelledby="recent-threads-title">
      <div className="archive-home__section-heading">
        <div><h2 id="recent-threads-title">{tx('最近归档', 'Recently archived')}</h2><span>{filtered.length} {tx('条抽样结果', 'threads sampled')}</span></div>
        <label>{tx('版块', 'Forum')} <select value={forum} onChange={event => void selectForum(event.target.value)}><option value="all">{tx('全部版块', 'All forums')}</option>{Object.entries(forumNames).map(([id, name]) => <option key={id} value={id}>{name} · {(data?.forum_counts[Number(id)] ?? 0).toLocaleString()}</option>)}</select></label>
      </div>
      <div className="archive-home__list">
        <div className="archive-home__list-head" aria-hidden="true">
          <span>{tx('帖子标题', 'Thread title')}</span>
          <span>{tx('版块', 'Forum')}</span>
          <span>{tx('回复', 'Replies')}</span>
          <span>{tx('发布日期', 'Published')}</span>
          <span>{tx('最后回复日期', 'Last reply')}</span>
        </div>
        {sampleLoading ? <LoadingIndicator className="archive-home__loading" label={tx('正在读取所选版块…', 'Loading this forum…')} /> : isLoading ? <LoadingIndicator className="archive-home__loading" label={tx('正在读取最近归档…', 'Loading recent archives…')} /> : filtered.length ? filtered.map(item => <Link key={item.tid} to={`/threads/${item.tid}`} className="archive-home__thread" title={formatThreadListTitle(item)}>
          <span className="archive-home__thread-main"><span className="archive-home__thread-title">{formatThreadListTitle(item)}</span><span className="archive-home__thread-id">#{item.tid}</span></span>
          <span className="archive-home__thread-forum">{forumNames[item.forum_id ?? -1] || tx('未分类', 'Uncategorized')}</span>
          <span className="archive-home__thread-replies">{item.reply_count ?? '—'}</span>
          <span className="archive-home__thread-date"><small>{tx('发布日期', 'Published')}</small>{formatDateTimeStacked(item.pub_time)}</span>
          <span className="archive-home__thread-date"><small>{tx('最后回复日期', 'Last reply')}</small>{formatDateTimeStacked(item.remote_last_reply_at)}</span>
        </Link>) : <p className="archive-home__empty">{data ? tx('当前筛选下没有最近归档。', 'No recent archives match this filter.') : tx('首页数据暂不可用。', 'Dashboard data is unavailable.')}</p>}
      </div>
      <div className="archive-home__footer"><Link to="/threads" className="archive-home__all-link">{tx('查看全部归档', 'View all archives')} <ArrowRight aria-hidden="true" /></Link></div>
    </section>
  </div>
}
