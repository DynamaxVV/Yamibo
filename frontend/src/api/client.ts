const BASE = '/api'

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || `${res.status} ${res.statusText}`)
  }
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
  url: string | null
  rerun_job_id: string | null
  rerun_job_status: string | null
  description: string
  description_en: string
  progress_current: number
  progress_total: number | null
  worker_id: string | null
  error_code: string | null
  error_message: string | null
  failure_kind: string | null
  paused_at: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
  payload?: Record<string, unknown>
  artifacts?: Record<string, unknown>
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

export interface ThreadListResponse {
  page: number
  page_size: number
  total_count: number
  total_pages: number
  q: string
  forum_id: number | null
  days: number | null
  archive_status: string | null
  sort_key: string
  sort_dir: string
  items: ThreadSummary[]
}

export interface JobListResponse {
  page: number
  page_size: number
  total_count: number
  total_pages: number
  status: string | null
  failure_kind: string | null
  items: JobSummary[]
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
  rag_summary?: {
    enabled: boolean
    chunk_count: number
    indexed_chunk_count: number
    pending_chunk_count: number
    failed_chunk_count: number
    last_indexed_at: string | null
    latest_job: {
      job_id: string
      status: string
      stage: string | null
      updated_at: string
      created_at: string
    } | null
  }
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
  remote_image_urls?: string[]
  missing_image_urls?: string[]
  image_slots?: Array<{
    remote_url: string
    local_path: string | null
    status: string
  }>
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
  archive_size_bytes?: number | null
  archive_size_updated_at?: string | null
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
  remote_access_pause: {
    active: boolean
    reason: string
    message: string
    source: string
    triggered_at: string
    paused_job_ids: string[]
    paused_job_count: number
    job_types: string[]
    context: Record<string, unknown>
  } | null
  job_control: {
    jobs_enabled: boolean
    queued: number
    running: number
    retrying: number
    interrupted: number
    paused: number
  }
}

export interface FontAsset {
  name: string
  label: string
  family: string
  url: string
}

export interface RagOverview {
  enabled: boolean
  config: {
    embedding_provider: string
    embedding_model: string
    embedding_dimensions: number
    chunker_version: string
    min_chunk_chars: number
    max_chunk_chars: number
    hybrid_fts_candidates: number
    hybrid_vector_candidates: number
  }
  index_meta: Record<string, string>
  counts: {
    thread_total: number
    indexed_threads: number
    unindexed_threads: number
    total_chunks: number
    indexed_chunks: number
    pending_chunks: number
    failed_chunks: number
  }
  forum_breakdown: Array<{
    forum_id: number
    name: string
    name_en: string | null
    content_kind: string
    thread_count: number
    indexed_thread_count: number
    chunk_count: number
  }>
  recent_jobs: JobSummary[]
}

export interface SettingsResponse {
  config_path: string
  values: Record<string, unknown>
  stored: Record<string, unknown>
  sources: Record<string, string>
  locked_fields: string[]
  effects: Record<string, 'immediate' | 'restart_daemon' | 'restart_web' | 'restart_daemon_web'>
}

export interface SettingsUpdateResponse extends SettingsResponse {
  ok: boolean
  saved_path: string
  restart_required: boolean
  restart_targets: Array<'daemon' | 'web'>
  effect_mode_summary: 'immediate' | 'restart_daemon' | 'restart_web' | 'restart_daemon_web'
}

export interface SettingsModelsResponse {
  models: string[]
}

export interface RagThreadRow {
  tid: number
  raw_title: string
  display_title: string
  publisher: string | null
  sync_time: string | null
  archive_status: string | null
  forum_id: number | null
  content_kind: string | null
  category: string | null
  rag_chunk_count: number
  rag_indexed_chunk_count: number
  rag_pending_chunk_count: number
  rag_failed_chunk_count: number
  rag_last_indexed_at: string | null
  rag_index_state: string
}

export interface RagThreadListResponse {
  index_state: string
  rag_status: string
  page: number
  page_size: number
  total_count: number
  total_pages: number
  items: RagThreadRow[]
}

export interface RagSearchItem {
  chunk_id: string
  tid: number
  pid: number | null
  floor_no: number | null
  display_title: string
  publisher: string | null
  pub_time: string | null
  content_kind: string | null
  snippet: string
  score: number
  score_parts: {
    keyword: number
    vector: number
    metadata: number
  }
  source_uri: string
}

export interface RagSearchResponse {
  query: string
  mode: string
  top_k: number
  count: number
  items: RagSearchItem[]
}

export interface AgentWireResult<T> {
  ok: boolean
  data?: T
  error?: {
    code: string
    message: string
    agent_hint: string
    retryable?: boolean
  }
  warnings?: string[]
}

export interface RagBatchIndexResult {
  ok: boolean
  job_type?: string
  target_count: number
  created_count: number
  reused_count: number
  created_job_ids: string[]
  reused_job_ids: string[]
  tids: number[]
}

export interface ThreadBatchArchiveResult {
  ok: boolean
  job_type: string
  target_count: number
  created_count: number
  reused_count: number
  created_job_ids: string[]
  reused_job_ids: string[]
  tids: number[]
}

export interface ThreadBatchDeleteResult {
  ok: boolean
  deleted: number
  tids: number[]
  deleted_series_ids?: number[]
}

export interface ThreadBatchResyncResult {
  ok: boolean
  job_type: string
  target_count: number
  created_count: number
  reused_count: number
  created_job_ids: string[]
  reused_job_ids: string[]
  tids: number[]
}

// ── Remote Forum Types ────────────────────────────────────────────────

export interface RemoteForum {
  forum_id: number
  name: string
  name_en: string | null
  content_kind: string
  enabled: boolean
}

export interface RemoteForumThreadLocal {
  archived: boolean
  sync_time: string | null
  series_id: number | null
  export_path: string | null
  content_kind: string | null
}

export interface RemoteForumThread {
  tid: number
  title: string
  display_title: string
  category: string | null
  row_kind: string
  publisher: string | null
  posted_at: string | null
  last_reply_at: string | null
  reply_count: number | null
  url: string
  archive_status: string | null
  local_thread: RemoteForumThreadLocal | null
}

export interface RemoteForumListResponse {
  source: string
  forum_id: number
  page: number
  order: string
  total_pages: number
  final_url: string
  items: RemoteForumThread[]
}

export interface RemoteThreadDetail {
  source: string
  tid: number
  forum_id: number | null
  page: number
  total_pages: number | null
  url: string
  raw_title: string
  display_title: string
  publisher: string | null
  publisher_uid: string | null
  pub_time: string | null
  content_kind: string | null
  archive_status: string | null
  local_thread: RemoteForumThreadLocal | null
  floors: FloorSummary[]
}

// API methods
export const api = {
  dashboard: (limit?: number) => fetchJson<DashboardData>(`/dashboard${limit ? `?limit=${limit}` : ''}`),
  jobs: (params?: { status?: string; failure_kind?: string; page?: number; page_size?: number }) => {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.failure_kind) qs.set('failure_kind', params.failure_kind)
    if (params?.page) qs.set('page', String(params.page))
    if (params?.page_size) qs.set('page_size', String(params.page_size))
    const s = qs.toString()
    return fetchJson<JobListResponse>(`/jobs${s ? `?${s}` : ''}`)
  },
  jobCounts: () => fetchJson<Record<string, number>>('/jobs/counts'),
  jobFailureCounts: (status?: string) => fetchJson<Record<string, number>>(`/jobs/failure-counts${status ? `?status=${status}` : ''}`),
  job: (id: string) => fetchJson<JobSummary>(`/jobs/${id}`),
  jobEvents: (id: string) => fetchJson<JobEvent[]>(`/jobs/${id}/events`),
  resumeRemoteAccess: () => postJson<{ ok: boolean; resumed_job_ids: string[]; resumed_job_count: number }>('/remote-access/resume', {}),
  controlJobs: (action: 'pause' | 'resume') => postJson<{ ok: boolean; action: string; changed_job_ids: string[]; changed_count: number; job_control: DashboardData['job_control'] }>('/jobs/control', { action }),
  threads: (params?: { q?: string; forum_id?: number; days?: number; archive_status?: string; sort_key?: string; sort_dir?: string; page?: number; page_size?: number }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.forum_id) qs.set('forum_id', String(params.forum_id))
    if (params?.days) qs.set('days', String(params.days))
    if (params?.archive_status) qs.set('archive_status', params.archive_status)
    if (params?.sort_key) qs.set('sort_key', params.sort_key)
    if (params?.sort_dir) qs.set('sort_dir', params.sort_dir)
    if (params?.page) qs.set('page', String(params.page))
    if (params?.page_size) qs.set('page_size', String(params.page_size))
    const s = qs.toString()
    return fetchJson<ThreadListResponse>(`/threads${s ? `?${s}` : ''}`)
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
  refreshForumSizeCache: () => postJson<{ ok: boolean; updated_at: string | null; forum_count: number }>('/forums/refresh-size-cache', {}),
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
  ragOverview: () => fetchJson<RagOverview>('/rag/overview'),
  settings: () => fetchJson<SettingsResponse>('/settings'),
  settingsModels: () => fetchJson<SettingsModelsResponse>('/settings/models'),
  updateSettings: (values: Record<string, unknown>) => postJson<SettingsUpdateResponse>('/settings', { values }),
  ragThreads: (params?: { q?: string; forum_id?: number | 'all'; index_state?: string; rag_status?: string; page?: number; page_size?: number }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.forum_id != null && params.forum_id !== 'all') qs.set('forum_id', String(params.forum_id))
    if (params?.index_state) qs.set('index_state', params.index_state)
    if (params?.rag_status && params.rag_status !== 'all') qs.set('rag_status', params.rag_status)
    if (params?.page) qs.set('page', String(params.page))
    if (params?.page_size) qs.set('page_size', String(params.page_size))
    const s = qs.toString()
    return fetchJson<RagThreadListResponse>(`/rag/threads${s ? `?${s}` : ''}`)
  },
  createRagIndex: (data: { tid?: number; force?: boolean; embedding_dimensions?: number }) =>
    postJson<AgentWireResult<{ job_id: string; tid: number | null; created: boolean; job_type: string }>>('/rag/index', data),
  createRagIndexBatch: (data: { tids?: number[]; q?: string; forum_id?: number | null; index_state?: string; rag_status?: string; force?: boolean }) =>
    postJson<RagBatchIndexResult>('/rag/index-batch', data),
  createThreadArchiveBatch: (data: { tids: number[]; forum_id?: number | null; base_url?: string | null }) =>
    postJson<ThreadBatchArchiveResult>('/threads/archive-batch', data),
  ragSearch: (data: {
    query: string
    mode: string
    top_k: number
    forum_id?: number | null
    content_kind?: string | null
    tid?: number | null
    series_id?: number | null
    floor_start?: number | null
    floor_end?: number | null
  }) => postJson<AgentWireResult<RagSearchResponse>>('/rag/search', data),
  resyncThread: (tid: number, forum_id?: number) => postJson<{ ok: boolean; job_id: string }>('/threads/resync', { tid, ...(forum_id ? { forum_id } : {}) }),
  exportThread: (tid: number, strategy?: string, forum_id?: number) => postJson<{ ok: boolean; job_id: string }>('/threads/export', { tid, strategy, ...(forum_id ? { forum_id } : {}) }),
  deleteThread: (tid: number) => postJson<{ ok: boolean; deleted_series_id?: number }>('/threads/delete', { tid }),
  deleteThreads: (tids: number[]) => postJson<ThreadBatchDeleteResult>('/threads/batch-delete', { tids }),
  resyncThreads: (tids: number[], base_url?: string | null) => postJson<ThreadBatchResyncResult>('/threads/resync-batch', { tids, ...(base_url ? { base_url } : {}) }),
  deleteJob: (job_id: string) => postJson<{ ok: boolean }>('/jobs/delete', { job_id }),
  batchDeleteJobs: (status: string) => postJson<{ ok: boolean; deleted: number }>('/jobs/batch-delete', { status }),
  batchDeleteJobIds: (jobIds: string[]) => postJson<{ ok: boolean; deleted: number }>('/jobs/batch-delete-ids', { job_ids: jobIds }),
  safeDeleteJob: (jobId: string) => postJson<{ ok: boolean; action: string; job_id: string }>('/jobs/safe-delete', { job_id: jobId }),
  retryJob: (jobId: string) => postJson<{ ok: boolean; job_id: string; source_job_id: string; status: string }>('/jobs/retry', { job_id: jobId }),
  pauseJob: (jobId: string) => postJson<{ ok: boolean; job_id: string; status: string }>('/jobs/pause', { job_id: jobId }),
  resumeJob: (jobId: string) => postJson<{ ok: boolean; job_id: string; status: string }>('/jobs/resume', { job_id: jobId }),
  updateChapter: (tid: number, chapter_name: string | null, chapter_index: number | null, author_guess?: string | null, group_name?: string | null) => postJson<{ ok: boolean }>('/threads/update-chapter', { tid, chapter_name, chapter_index, author_guess, group_name }),
  remoteForums: () => fetchJson<RemoteForum[]>('/remote/forums'),
  remoteForum: (params: { forum_id?: number; page?: number; order?: string }) => {
    const qs = new URLSearchParams()
    if (params.forum_id) qs.set('forum_id', String(params.forum_id))
    if (params.page) qs.set('page', String(params.page))
    if (params.order) qs.set('order', params.order)
    const s = qs.toString()
    return fetchJson<RemoteForumListResponse>(`/remote/forum${s ? `?${s}` : ''}`)
  },
  remoteThread: (tid: number, params?: { forum_id?: number; page?: number }) => {
    const qs = new URLSearchParams()
    if (params?.forum_id) qs.set('forum_id', String(params.forum_id))
    if (params?.page) qs.set('page', String(params.page))
    const s = qs.toString()
    return fetchJson<RemoteThreadDetail>(`/remote/threads/${tid}${s ? `?${s}` : ''}`)
  },
}
