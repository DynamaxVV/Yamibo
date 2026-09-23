import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, BookOpen, Download, Library, ListTodo } from 'lucide-react'
import { api, type DashboardData, type ThreadSummary } from '../api/client'
import packageInfo from '../../package.json'
import { useI18n } from '../context/I18nContext'
import { formatThreadListTitle } from '../utils/threadTitle'
import '../styles/dashboard-editorial.css'

export function Dashboard() {
  const { lang, tx } = useI18n()
  const currentDate = new Intl.DateTimeFormat(lang === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric', month: 'short', day: '2-digit',
  }).format(new Date())
  const [data, setData] = useState<DashboardData | null>(null)
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [forumNames, setForumNames] = useState<Record<number, string>>({})
  const [forum, setForum] = useState('all')
  const [page, setPage] = useState(1)
  const [error, setError] = useState(false)

  useEffect(() => {
    api.forums().then(items => setForumNames(Object.fromEntries(items.map(item => [item.forum_id, lang === 'en' ? item.name_en || item.name : item.name])))).catch(() => {})
  }, [lang])

  useEffect(() => {
    let active = true
    Promise.all([api.dashboard(50), ...[30, 55, 33, 5].map(forum_id => api.threads({ forum_id, page_size: 20 }))])
      .then(([summary, ...results]) => {
        if (!active) return
        setData(summary)
        setThreads(results.flatMap(result => result.items).sort((a, b) => (b.sync_time || '').localeCompare(a.sync_time || '')))
        setError(false)
      })
      .catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [])

  const filtered = useMemo(() => forum === 'all' ? threads : threads.filter(item => item.forum_id === Number(forum)), [threads, forum])
  const visible = filtered.slice((page - 1) * 10, page * 10)
  const pages = Math.max(1, Math.ceil(filtered.length / 10))
  const stats = [
    { label: tx('归档帖子', 'Archived threads'), value: data?.thread_count, to: '/threads', icon: BookOpen },
    { label: tx('作品系列', 'Series'), value: data?.series_count, to: '/series', icon: Library },
    { label: tx('导出档案', 'Exports'), value: data?.export_count, to: '/exports', icon: Download },
  ]
  const forumCounts = Object.entries(data?.forum_counts ?? {})
    .map(([id, count]) => ({ id: Number(id), count }))
    .filter(({ count }) => count > 0)
    .sort((a, b) => b.count - a.count)

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
        <ListTodo aria-hidden="true" /><span><strong>{data?.job_control.running == null ? '—' : data.job_control.running.toLocaleString()}</strong><small>{data ? `${data.job_control.jobs_enabled ? tx('运行任务', 'Running tasks') : tx('队列已暂停', 'Queue paused')} · ${data.job_control.paused} ${tx('个暂停', 'paused')}` : tx('运行任务', 'Running tasks')}</small></span>
      </Link>
    </div>
    {forumCounts.length > 0 && <nav className="archive-home__forum-counts" aria-label={tx('各分区归档帖子数', 'Archived threads by forum')}>
      <strong>{tx('分区收录', 'By forum')}</strong>
      {forumCounts.map(({ id, count }) => <Link key={id} to={`/threads?forum_id=${id}`} title={tx(`查看${forumNames[id] || `版块 #${id}`}的归档帖子`, `View archived threads in ${forumNames[id] || `forum #${id}`}`)}>
        <span>{forumNames[id] || tx(`版块 #${id}`, `Forum #${id}`)}</span><b>{count.toLocaleString()}</b>
      </Link>)}
    </nav>}
    {data && (data.job_control.interrupted > 0 || data.job_control.paused > 0) && <Link to="/jobs" className="archive-home__notice"><strong>{tx('待处理任务', 'Tasks needing attention')}</strong><span>{data.job_control.interrupted} {tx('个中断', 'interrupted')}，{data.job_control.paused} {tx('个暂停', 'paused')}</span><ArrowRight aria-hidden="true" /></Link>}
    <section className="archive-home__recent" aria-labelledby="recent-threads-title">
      <div className="archive-home__section-heading">
        <div><h2 id="recent-threads-title">{tx('最近归档', 'Recently archived')}</h2><span>{filtered.length} {tx('条抽样结果', 'threads sampled')}</span></div>
        <label>{tx('版块', 'Forum')} <select value={forum} onChange={event => { setForum(event.target.value); setPage(1) }}><option value="all">{tx('全部版块', 'All forums')}</option>{Object.entries(forumNames).map(([id, name]) => <option key={id} value={id}>{name} · {(data?.forum_counts[Number(id)] ?? 0).toLocaleString()}</option>)}</select></label>
      </div>
      <div className="archive-home__list">
        <div className="archive-home__list-head" aria-hidden="true"><span>{tx('帖子标题', 'Thread title')}</span><span>{tx('版块', 'Forum')}</span><span>{tx('回复', 'Replies')}</span><span>{tx('收录日期', 'Archived')}</span></div>
        {visible.length ? visible.map(item => <Link key={item.tid} to={`/threads/${item.tid}`} className="archive-home__thread" title={formatThreadListTitle(item)}>
          <span className="archive-home__thread-main"><span className="archive-home__thread-title">{formatThreadListTitle(item)}</span><span className="archive-home__thread-id">#{item.tid}</span></span>
          <span className="archive-home__thread-forum">{forumNames[item.forum_id ?? -1] || tx('未分类', 'Uncategorized')}</span>
          <span className="archive-home__thread-replies">{item.reply_count ?? '—'}</span>
          <span className="archive-home__thread-date">{item.sync_time?.slice(0, 10) || tx('时间未知', 'Unknown date')}</span>
        </Link>) : <p className="archive-home__empty">{data ? tx('当前筛选下没有最近归档。', 'No recent archives match this filter.') : tx('正在读取最近归档…', 'Loading recent archives…')}</p>}
      </div>
      <div className="archive-home__footer"><Link to="/threads" className="archive-home__all-link">{tx('查看全部归档', 'View all archives')} <ArrowRight aria-hidden="true" /></Link><div className="archive-home__pagination"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>{tx('上一页', 'Previous')}</button><span>{page} / {pages}</span><button disabled={page >= pages} onClick={() => setPage(page + 1)}>{tx('下一页', 'Next')}</button></div></div>
    </section>
  </div>
}
