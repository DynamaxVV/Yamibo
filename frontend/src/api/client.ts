const BASE = '/api'

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export interface JobSummary {
  job_id: string
  job_type: string
  status: string
  stage: string | null
  tid: number | null
  description: string
  description_en: string
  payload: Record<string, unknown>
  artifacts: Record<string, unknown>
  progress_current: number
  progress_total: number | null
  worker_id: string | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
}

export interface JobEvent {
  event_id: number
  job_id: string
  event_type: string
  status: string | null
  stage: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface ThreadSummary {
  tid: number
  raw_title: string
  display_title: string
  publisher: string | null
  pub_time: string | null
  sync_time: string | null
  archive_status: string | null
  validation_status: string | null
  context_path: string | null
  series_id: number | null
  export_path: string | null
  forum_id: number | null
  content_kind: string | null
  category: string | null
  core_title_guess: string | null
  series_key: string | null
  chapter_name: string | null
  reply_count: number
}

export interface ThreadDetail extends ThreadSummary {
  url: string | null
  publisher_uid: string | null
  pub_time: string | null
  image_count: number
  primary_media_type: string | null
  archive_summary?: ArchiveSummary
  missing_image_urls?: string[]
  floors: FloorSummary[]
  floor_count?: number
  floor_page?: number | null
  floor_page_size?: number | null
  floor_total_pages?: number | null
  series_title: string | null
  chapter_index: number | null
  group_name: string | null
  author_guess: string | null
}

export interface ArchiveSummary {
  context_path?: string | null
  archived_images?: Record<string, string[]>
  non_export_images?: Record<string, string[]>
  shared_images?: Record<string, string[]>
  skipped_image_urls?: Record<string, string[]>
  missing_image_urls?: string[]
  missing_shared_image_urls?: string[]
}

export interface FloorSummary {
  pid: number
  floor_no: number
  publisher: string | null
  publisher_uid: string | null
  content: string | null
  pub_time: string | null
  has_images: boolean
  quote_text: string | null
  reply_text: string | null
  rich_body_html: string | null
}

export interface Asset {
  asset_id: string
  tid: number
  pid: number
  asset_type: string
  remote_url: string
  local_path: string | null
  exportable: boolean
  required: boolean
  status: string
}

export interface ContentBlock {
  id: number
  tid: number
  pid: number
  order_index: number
  block_type: string
  text: string | null
  asset_id: string | null
}

export interface SeriesSummary {
  series_id: number
  canonical_title: string | null
  series_key: string | null
  author_guess: string | null
  thread_count: number
  needs_review: number
  last_sync_time: string | null
}

export interface Forum {
  forum_id: number
  name: string
  name_en: string | null
  content_kind: string
  thread_count: number
  enabled: boolean
}

export interface WorkerHeartbeat {
  worker_id: string
  running_jobs: number
  seen_jobs: number
  latest_heartbeat_at: string | null
}

export interface AuditEvent {
  event_id: string
  actor: string
  action: string
  target_type: string
  target_id: string
  created_at: string
  description: string
  description_en: string
}

export interface ThreadImage {
  pid: number
  url: string
  source: string
  is_content: boolean
  is_shared: boolean
}

export interface LogEntry {
  ts: number
  level: string
  logger: string
  msg: string
}

export interface DashboardData {
  thread_count: number
  series_count: number
  export_count: number
  forum_counts: Record<number, number>
  recent_jobs: JobSummary[]
  live_thread_statuses: Record<number, string>
  workers: WorkerHeartbeat[]
  recent_audits: AuditEvent[]
  recent_threads: ThreadSummary[]
}

export interface FontAsset {
  name: string
  label: string
  family: string
  url: string
}

// API methods
export const api = {
  dashboard: (limit?: number) => fetchJson<DashboardData>(`/dashboard${limit ? `?limit=${limit}` : ''}`),
  jobs: (status?: string) => fetchJson<JobSummary[]>(`/jobs${status ? `?status=${status}` : ''}`),
  jobCounts: () => fetchJson<Record<string, number>>('/jobs/counts'),
  job: (id: string) => fetchJson<JobSummary>(`/jobs/${id}`),
  jobEvents: (id: string) => fetchJson<JobEvent[]>(`/jobs/${id}/events`),
  threads: (params?: { q?: string; forum_id?: number; days?: number }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.forum_id) qs.set('forum_id', String(params.forum_id))
    if (params?.days) qs.set('days', String(params.days))
    const s = qs.toString()
    return fetchJson<ThreadSummary[]>(`/threads${s ? `?${s}` : ''}`)
  },
  thread: (tid: number, params?: { preview_page?: number; preview_page_size?: number }) => {
    const qs = new URLSearchParams()
    if (params?.preview_page) qs.set('preview_page', String(params.preview_page))
    if (params?.preview_page_size) qs.set('preview_page_size', String(params.preview_page_size))
    const s = qs.toString()
    return fetchJson<ThreadDetail>(`/threads/${tid}${s ? `?${s}` : ''}`)
  },
  threadAssets: (tid: number) => fetchJson<Asset[]>(`/threads/${tid}/assets`),
  threadBlocks: (tid: number) => fetchJson<ContentBlock[]>(`/threads/${tid}/blocks`),
  threadImages: (tid: number) => fetchJson<ThreadImage[]>(`/threads/${tid}/images`),
  series: () => fetchJson<SeriesSummary[]>('/series'),
  seriesDetail: (id: number) => fetchJson<{ series: SeriesSummary; threads: ThreadSummary[] }>(`/series/${id}`),
  deleteSeries: (seriesId: number) => postJson<{ ok: boolean }>('/series/delete', { series_id: seriesId }),
  similarSeries: (seriesId: number) => fetchJson<SeriesSummary[]>(`/series/${seriesId}/similar`),
  forums: () => fetchJson<Forum[]>('/forums'),
  fonts: () => fetchJson<FontAsset[]>('/fonts'),
  exports: () => fetchJson<ThreadSummary[]>('/exports'),
  reviewItems: () => fetchJson<{ titles: ThreadSummary[]; series: SeriesSummary[] }>('/review'),
  confirmSeries: (seriesId: number) => postJson<{ ok: boolean }>('/review/confirm-series', { series_id: seriesId }),
  mergeSeries: (sourceId: number, targetId: number) => postJson<{ ok: boolean }>('/review/merge-series', { source_series_id: sourceId, target_series_id: targetId }),
  confirmTitle: (tid: number) => postJson<{ ok: boolean }>('/review/confirm-title', { tid }),
  updateTitle: (data: Record<string, unknown>) => postJson<{ ok: boolean }>('/review/update-title', data),
  updateSeries: (data: Record<string, unknown>) => postJson<{ ok: boolean }>('/review/update-series', data),
  rebuildSeries: () => postJson<{ ok: boolean; job_id: string }>('/review/rebuild-series', {}),
  debugInfo: () => fetchJson<Record<string, unknown>>('/debug/info'),
  logs: (limit?: number, since?: number) => {
    const qs = new URLSearchParams()
    if (limit) qs.set('limit', String(limit))
    if (since) qs.set('since', String(since))
    const s = qs.toString()
    return fetchJson<{ entries: LogEntry[]; count: number }>(`/logs${s ? `?${s}` : ''}`)
  },
  resyncThread: (tid: number, forum_id?: number) => postJson<{ ok: boolean; job_id: string }>('/threads/resync', { tid, ...(forum_id ? { forum_id } : {}) }),
  exportThread: (tid: number, strategy?: string, forum_id?: number) => postJson<{ ok: boolean; job_id: string }>('/threads/export', { tid, strategy, ...(forum_id ? { forum_id } : {}) }),
  deleteThread: (tid: number) => postJson<{ ok: boolean; deleted_series_id?: number }>('/threads/delete', { tid }),
  deleteJob: (job_id: string) => postJson<{ ok: boolean }>('/jobs/delete', { job_id }),
  batchDeleteJobs: (status: string) => postJson<{ ok: boolean; deleted: number }>('/jobs/batch-delete', { status }),
  batchDeleteJobIds: (jobIds: string[]) => postJson<{ ok: boolean; deleted: number }>('/jobs/batch-delete-ids', { job_ids: jobIds }),
  safeDeleteJob: (jobId: string) => postJson<{ ok: boolean; action: string; job_id: string }>('/jobs/safe-delete', { job_id: jobId }),
  updateChapter: (tid: number, chapter_name: string | null, chapter_index: number | null, author_guess?: string | null, group_name?: string | null) => postJson<{ ok: boolean }>('/threads/update-chapter', { tid, chapter_name, chapter_index, author_guess, group_name }),
}
