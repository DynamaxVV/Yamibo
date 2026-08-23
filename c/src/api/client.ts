const BASE = '/api'

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw toApiError(err, res.status, res.statusText)
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
    throw toApiError(err, res.status, res.statusText)
  }
  return res.json()
}

function toApiError(payload: unknown, status: number, statusText: string): Error {
  const root = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
  const detail = root.error && typeof root.error === 'object' ? root.error as Record<string, unknown> : root
  const message = typeof detail.message === 'string' ? detail.message : typeof root.detail === 'string' ? root.detail : typeof root.error === 'string' ? root.error : `${status} ${statusText}`
  const code = typeof detail.code === 'string' ? detail.code : undefined
  const error = new Error(message) as Error & { code?: string }
  if (code) error.code = code
  return error
}

export interface JobSummary {
  job_id: string
  job_type: string
  status: string
  stage: string | null
  tid: number | null
  url: string | null
  description: string
  description_en: string
  progress_current: number
  progress_total: number | null
  worker_id: string | null
  error_code: string | null
  error_message: string | null
  failure_kind: string | null
  active_error: { code: string | null; message: string | null } | null
  retry_count: number
  max_retries: number
  lease_until: string | null
  remote_attempt: Record<string, unknown> | null
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
  author_guess: string | null
  group_name: string | null
  floor_count: number
  local_reply_count: number | null
  reply_count_checked_at: string | null
  reply_count_mismatch_reason: string | null
  remote_last_reply_at_raw: string | null
  remote_last_reply_at: string | null
  remote_last_replier: string | null
  remote_reply_count: number | null
  remote_observed_at: string | null
  remote_observed_from: string | null
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

export interface ActiveSyncJob {
  job_id: string
  job_type: 'sync_thread'
  tid: number | null
  status: string
  stage: string | null
  updated_at: string
}

export interface ActiveSyncJobResponse {
  job: ActiveSyncJob | null
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
    asset_id?: string
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

export interface SignInAccountStats {
  account_id: string
  recent_checkin: string | null
  month_days: number | null
  consecutive_days: number | null
  total_days: number | null
  level: string | null
  today_status: 'checked' | 'not_checked' | 'unavailable'
  error: string | null
}

export interface SignInStats {
  fetched_at: string
  accounts: SignInAccountStats[]
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
  ts: string
  level: string
  service: string
  component: string
  event_type: string
  message: string
  result: string
  status: string
  job_id?: string
  job_type?: string
  tid?: number
  forum_id?: number
  run_id?: string
  stage?: string
  worker_id?: string
  error_code?: string
  error_message?: string
  retryable?: boolean
  attempt?: number
  duration_ms?: number
  fallback_mode?: string
  fallback_reason?: string
  warning_codes?: string[]
  agent_hint?: string
  next_action?: string
  trace_id?: string
  correlation_id?: string
  tags?: string[]
  payload?: Record<string, unknown>
  context?: Record<string, unknown>
  data_version?: string
  operation?: string
  resource_uri?: string
}

export interface LogQuery {
  limit?: number
  since?: string
  job_id?: string
  tid?: number
  event_type?: string
  level?: string
  component?: string
  q?: string
  errors_only?: boolean
}

export interface LogsResponse {
  entries: LogEntry[]
  count: number
  oldest_ts: string | null
  newest_ts: string | null
  applied_filters: Record<string, unknown>
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

export interface ProxyPoolNode {
  name: string
  display_name?: string
  delay_ms: number | null
  forum_delay_ms?: number | null
  network_delay_ms?: number | null
  error: string | null
  status: 'usable' | 'filtered' | 'blacklisted' | 'forum_blocked' | 'unreachable' | string
  blacklisted: boolean
  filtered?: boolean
  yamibo_accessible: boolean | null
  is_direct?: boolean
}

export interface ProxyPoolHealth {
  ok: boolean
  error?: string
  config?: { enabled: boolean; selector_group: string; test_timeout_ms: number }
  selector_group?: { exists: boolean; current_node: string | null; node_count: number }
  nodes: ProxyPoolNode[]
  probe_url?: string
  cached?: boolean
  cache_age_seconds?: number | null
}

export interface DaemonStatus {
  operational_mode: 'active' | 'idle' | 'paused' | 'remote_paused'
  jobs_enabled: boolean
  job_counts: {
    queued: number
    running: number
    retrying: number
    interrupted: number
    paused: number
  }
  daemon_alive: boolean
  worker_count: number
  remote_access_paused: boolean
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
  configured?: Record<string, boolean>
}

export interface TableLayout {
  key: string
  visible: boolean
  width: number
}

export interface TableLayouts {
  threads: TableLayout[]
  jobs: TableLayout[]
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
  disabled?: boolean
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
  core_title_guess: string | null
  chapter_name: string | null
  author_guess: string | null
  group_name: string | null
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
  content_kind: string | null
  core_title_guess: string | null
  chapter_name: string | null
  author_guess: string | null
  group_name: string | null
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

export interface BackfillStatus {
  enabled: boolean
  dry_run: boolean
  forum_id: number
  daily_limit: number
  interval_seconds: number
  max_pages: number
  today_count: number
  pending_count: number
  last_enqueued_at: string | null
  last_reason: string | null
}

// API methods
export const api = {
  dashboard: (limit?: number) => fetchJson<DashboardData>(`/dashboard${limit ? `?limit=${limit}` : ''}`),
  proxyPoolHealth: (forceRefresh = false) => fetchJson<ProxyPoolHealth>(`/proxy-pool/health${forceRefresh ? '?refresh=true' : ''}`),
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
  daemonStatus: () => fetchJson<DaemonStatus>('/daemon/status'),
  controlJobs: (action: 'pause' | 'resume') => postJson<{ ok: boolean; action: string; changed_job_ids: string[]; changed_count: number; job_control: DashboardData['job_control'] }>('/jobs/control', { action }),
  backfillStatus: () => fetchJson<BackfillStatus>('/jobs/backfill-status'),
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
  activeSyncJob: (tid: number) => fetchJson<ActiveSyncJobResponse>(`/threads/${tid}/active-sync-job`),
  threadAssets: (tid: number) => fetchJson<Asset[]>(`/threads/${tid}/assets`),
  threadBlocks: (tid: number) => fetchJson<ContentBlock[]>(`/threads/${tid}/blocks`),
  threadImages: (tid: number) => fetchJson<ThreadImage[]>(`/threads/${tid}/images`),
  retryThreadImage: (tid: number, asset_id: string) =>
    postJson<{ ok: boolean; job_id: string; status: string; created: boolean }>(`/threads/${tid}/images/${encodeURIComponent(asset_id)}/retry`, {}),
  series: () => fetchJson<SeriesSummary[]>('/series'),
  seriesDetail: (id: number) => fetchJson<{ series: SeriesSummary; threads: ThreadSummary[] }>(`/series/${id}`),
  deleteSeries: (seriesId: number) => postJson<{ ok: boolean }>('/series/delete', { series_id: seriesId }),
  similarSeries: (seriesId: number) => fetchJson<SeriesSummary[]>(`/series/${seriesId}/similar`),
  forums: () => fetchJson<Forum[]>('/forums'),
  signInAccounts: () => fetchJson<{ accounts: SignInAccountStats[] }>('/forums/sign-in-accounts'),
  signInStats: (accountId?: string) => fetchJson<SignInStats>(`/forums/sign-in-stats${accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''}`),
  signIn: (accountId: string) => postJson<{ ok: boolean; account_id: string; today_status: string }>('/forums/sign-in', { account_id: accountId }),
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
  logs: (query?: number | LogQuery, legacySince?: number | string) => {
    const params: LogQuery = typeof query === 'number'
      ? {
          limit: query,
          since: typeof legacySince === 'number'
            ? new Date(legacySince * 1000).toISOString()
            : legacySince,
        }
      : (query || {})
    const qs = new URLSearchParams()
    if (params.limit) qs.set('limit', String(params.limit))
    if (params.since) qs.set('since', params.since)
    if (params.job_id) qs.set('job_id', params.job_id)
    if (params.tid != null) qs.set('tid', String(params.tid))
    if (params.event_type) qs.set('event_type', params.event_type)
    if (params.level) qs.set('level', params.level)
    if (params.component) qs.set('component', params.component)
    if (params.q) qs.set('q', params.q)
    if (params.errors_only) qs.set('errors_only', 'true')
    const s = qs.toString()
    return fetchJson<LogsResponse>(`/logs${s ? `?${s}` : ''}`)
  },
  ragOverview: () => fetchJson<RagOverview>('/rag/overview'),
  settings: () => fetchJson<SettingsResponse>('/settings'),
  settingsModels: () => fetchJson<SettingsModelsResponse>('/settings/models'),
  updateSettings: (values: Record<string, unknown>) => postJson<SettingsUpdateResponse>('/settings', { values }),
  testHermesConnection: () => postJson<{ connected: boolean; endpoint: string; label: string; status: number; stdout: string; stderr: string; request?: unknown }>('/settings/hermes-test', {}),
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
  batchDeleteJobs: (status: string, failureKind?: string) => postJson<{ ok: boolean; deleted: number }>('/jobs/batch-delete', { status, ...(failureKind ? { failure_kind: failureKind } : {}) }),
  batchDeleteJobIds: (jobIds: string[]) => postJson<{ ok: boolean; deleted: number }>('/jobs/batch-delete-ids', { job_ids: jobIds }),
  safeDeleteJob: (jobId: string) => postJson<{ ok: boolean; action: string; job_id: string }>('/jobs/safe-delete', { job_id: jobId }),
  retryJob: (jobId: string) => postJson<{ ok: boolean; job_id: string; source_job_id: string; status: string }>('/jobs/retry', { job_id: jobId }),
  pauseJob: (jobId: string) => postJson<{ ok: boolean; job_id: string; status: string }>('/jobs/pause', { job_id: jobId }),
  resumeJob: (jobId: string) => postJson<{ ok: boolean; job_id: string; status: string }>('/jobs/resume', { job_id: jobId }),
  updateChapter: (
    tid: number,
    display_title: string | null,
    chapter_name: string | null,
    chapter_index: number | null,
    author_guess?: string | null,
    group_name?: string | null,
  ) => postJson<{ ok: boolean }>('/threads/update-chapter', { tid, display_title, chapter_name, chapter_index, author_guess, group_name }),
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
  chatContext: () => fetchJson<import('../types/chat').ChatContext>('/chat/context'),
  chatSessions: (params?: { limit?: number; offset?: number }, signal?: AbortSignal) => fetchJson<import('../types/chat').ChatSessionPage>(`/chat/sessions?limit=${params?.limit ?? 50}&offset=${params?.offset ?? 0}`, { signal }),
  createChatSession: (title?: string) => postJson<import('../types/chat').ChatSession>('/chat/sessions', title ? { title } : {}),
  getChatSession: (id: string) => fetchJson<import('../types/chat').ChatSession>(`/chat/sessions/${encodeURIComponent(id)}`),
  renameChatSession: (id: string, title: string) => fetchJson<import('../types/chat').ChatSession>(`/chat/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  deleteChatSession: (id: string) => fetchJson<{ ok: boolean }>(`/chat/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  chatMessages: async (id: string, signal?: AbortSignal) => { const payload = await fetchJson<{ messages?: import('../types/chat').ChatMessage[] } | import('../types/chat').ChatMessage[]>(`/chat/sessions/${encodeURIComponent(id)}/messages`, { signal }); return Array.isArray(payload) ? payload : payload.messages || [] },
  startChatRun: (id: string, input: string) => postJson<import('../types/chat').StartRunResponse>(`/chat/sessions/${encodeURIComponent(id)}/runs`, { input }),
  getChatRun: (id: string) => fetchJson<import('../types/chat').ChatRun>(`/chat/runs/${encodeURIComponent(id)}`),
  stopChatRun: (id: string) => postJson<{ status: 'stopping' }>(`/chat/runs/${encodeURIComponent(id)}/stop`, {}),
  approveChatRun: (id: string, choice: import('../types/chat').ApprovalChoice, resolve_all = false) => postJson<{ status: string }>(`/chat/runs/${encodeURIComponent(id)}/approval`, { choice, resolve_all }),
  openChatRunEvents: async (id: string, lastEventId?: string, signal?: AbortSignal) => {
    const headers: HeadersInit = { Accept: 'text/event-stream' }
    if (lastEventId) headers['Last-Event-ID'] = lastEventId
    const res = await fetch(`${BASE}/chat/runs/${encodeURIComponent(id)}/events`, { headers, signal })
    if (!res.ok || !res.body) { const payload = await res.json().catch(() => null); throw toApiError(payload, res.status, res.statusText) }
    return res.body
  },
}
