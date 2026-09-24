import { Fragment, type ReactNode } from 'react'

const BBS_URL_RE = /https:\/\/bbs\.yamibo\.com[^\s"'<>）)\]]*/g

export type JobFailureKind =
  | 'cancelled'
  | 'local_missing'
  | 'empty_content'
  | 'forum_closed'
  | 'thread_deleted'
  | 'thread_permission'
  | 'thread_missing'
  | 'login_required'
  | 'maintenance'
  | 'remote_http_404'
  | 'remote_http_error'
  | 'remote_timeout'
  | 'remote_connection'
  | 'remote_blocked'
  | 'remote_fetch'
  | 'unexpected_page'
  | 'image_download_failed'
  | 'image_http_404'
  | 'image_upstream_throttled'
  | 'image_validation_failed'
  | 'image_truncated_transfer'
  | 'validation'
  | 'other'

const JOB_FAILURE_KIND_LABELS: Record<'zh' | 'en', Record<JobFailureKind, string>> = {
  zh: {
    cancelled: '已取消',
    local_missing: '本地缺档',
    empty_content: '正文为空',
    forum_closed: '查无此区/已关闭',
    thread_deleted: '帖子已删除',
    thread_permission: '帖子权限不足',
    thread_missing: '主题不存在',
    login_required: '需要登录',
    maintenance: '论坛维护',
    remote_http_404: 'HTTP 404',
    remote_http_error: 'HTTP 错误',
    remote_timeout: '请求超时',
    remote_connection: '连接错误',
    remote_blocked: '反爬拦截',
    remote_fetch: '远程抓取失败',
    unexpected_page: '页面不符合预期',
    image_download_failed: '图片下载失败',
    image_http_404: '图片地址返回 404',
    image_upstream_throttled: '图片源站限流或暂不可用',
    image_validation_failed: '图片内容校验失败',
    image_truncated_transfer: '图片传输不完整',
    validation: '校验失败',
    other: '其他失败',
  },
  en: {
    cancelled: 'Cancelled',
    local_missing: 'Local archive missing',
    empty_content: 'Empty content',
    forum_closed: 'Forum closed',
    thread_deleted: 'Thread deleted',
    thread_permission: 'Thread permission denied',
    thread_missing: 'Thread missing',
    login_required: 'Login required',
    maintenance: 'Maintenance',
    remote_http_404: 'HTTP 404',
    remote_http_error: 'HTTP Error',
    remote_timeout: 'Timeout',
    remote_connection: 'Connection Error',
    remote_blocked: 'Blocked',
    remote_fetch: 'Remote fetch failed',
    unexpected_page: 'Unexpected page',
    image_download_failed: 'Image download failed',
    image_http_404: 'Image URL returned 404',
    image_upstream_throttled: 'Image source rate limited or unavailable',
    image_validation_failed: 'Image body failed validation',
    image_truncated_transfer: 'Incomplete image transfer',
    validation: 'Validation failed',
    other: 'Other failure',
  },
}

type JobFailureLike = {
  error_code?: string | null
  error_message?: string | null
  artifacts?: Record<string, unknown> | null
}

function getRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function getString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function includesAny(text: string, parts: string[]): boolean {
  return parts.some(part => text.includes(part))
}

export function renderBbsLinks(text: string): ReactNode {
  const parts: ReactNode[] = []
  let lastIndex = 0
  for (const match of text.matchAll(BBS_URL_RE)) {
    const url = match[0]
    const index = match.index || 0
    if (index > lastIndex) {
      parts.push(text.slice(lastIndex, index))
    }
    parts.push(
      <a key={`${index}:${url}`} href={url} target="_blank" rel="noreferrer">
        {url}
      </a>,
    )
    lastIndex = index + url.length
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }
  return parts.length > 0 ? <Fragment>{parts}</Fragment> : text
}

export function formatJobFailureKind(kind: string | null | undefined, lang: 'zh' | 'en'): string {
  if (!kind) return '-'
  return JOB_FAILURE_KIND_LABELS[lang][kind as JobFailureKind] || JOB_FAILURE_KIND_LABELS[lang].other
}

export function getJobFailureKind(job: JobFailureLike): JobFailureKind | null {
  const errorCode = getString(job.error_code).toLowerCase()
  const errorMessage = getString(job.error_message)
  const artifacts = getRecord(job.artifacts)
  const imageSummary = artifacts ? getRecord(artifacts.image_download_summary) : null
  const failureContext = artifacts ? getRecord(artifacts.failure_context) : null
  const remoteFetch = failureContext ? getRecord(failureContext.remote_fetch) : null
  const remoteAttempt = artifacts ? getRecord(artifacts.remote_attempt) : null
  const remoteDetails = remoteFetch || remoteAttempt
  const pageType = getString(remoteDetails?.page_type).toLowerCase()
  const promptText = getString(remoteDetails?.prompt_text)
  const combined = `${errorCode} ${errorMessage} ${pageType} ${promptText}`.toLowerCase()

  if (errorCode === 'cancelled' || combined.includes('was cancelled by user')) return 'cancelled'
  if (includesAny(combined, ['local_archive_not_found', 'export_precheck_failed', 'not archived locally', 'thread archive is partial', 'thread archive is not complete'])) return 'local_missing'
  if (includesAny(combined, ['content is required when no images are present'])) return 'empty_content'
  if (pageType === 'prompt_forum_closed' || includesAny(combined, ['查无此区', '此区已关闭', '版块已关闭'])) return 'forum_closed'
  if (includesAny(combined, ['本帖已经删除', 'thread_deleted'])) return 'thread_deleted'
  if (pageType === 'prompt_thread_missing_or_removed_or_review' || includesAny(combined, ['指定的主题不存在', '正在被审核'])) return 'thread_missing'
  if (includesAny(combined, ['已被删除'])) return 'thread_deleted'
  // 权限不足（非 255 的删除码 / 群组限制 / 用户组升级）
  if (includesAny(errorCode, ['remote_thread_permission_required', 'group_access_denied', 'user_group_upgrade_required'])) return 'thread_permission'
  if (includesAny(errorCode, ['loginrequirederror', 'remote_login_required']) || includesAny(combined, ['login required', 'login_required'])) return 'login_required'
  // 维护 / 反爬暂停
  if (includesAny(errorCode, ['remotemaintenanceerror', 'remote_maintenance', 'remote_access_paused']) || combined.includes('maintenance')) return 'maintenance'
  // 远程抓取类 — 细分
  if (includesAny(errorCode, ['remote_http_404'])) return 'remote_http_404'
  if (includesAny(errorCode, ['remote_http_403', 'remote_http_5xx', 'remote_http_xxx'])) return 'remote_http_error'
  if (includesAny(errorCode, ['remote_timeout'])) return 'remote_timeout'
  if (includesAny(errorCode, ['remote_connection_error'])) return 'remote_connection'
  if (includesAny(errorCode, ['remote_soft_block'])) return 'remote_blocked'
  if (includesAny(errorCode, ['image_target_not_downloaded'])) {
    const failed = Number(imageSummary?.failed || 0)
    const statuses = getRecord(imageSummary?.http_status_counts)
    const errors = getRecord(imageSummary?.error_type_counts)
    const statusKeys = statuses ? Object.keys(statuses) : []
    if (failed > 0 && statuses && errors && Object.keys(errors).length === 1 && Number(errors.http_error) === failed && statusKeys.reduce((total, key) => total + Number(statuses[key] || 0), 0) === failed) {
      if (statusKeys.length === 1 && statusKeys[0] === '404') return 'image_http_404'
      if (statusKeys.length > 0 && statusKeys.every(key => key === '429' || key === '503')) return 'image_upstream_throttled'
    }
    const errorKeys = errors ? Object.keys(errors) : []
    if (failed > 0 && errors && errorKeys.length > 0 && errorKeys.every(key => key === 'truncated_image' || key === 'invalid_image_body') && errorKeys.reduce((total, key) => total + Number(errors[key] || 0), 0) === failed) {
      const diagnostics = Array.isArray(artifacts?.image_download_diagnostics) ? artifacts.image_download_diagnostics : []
      if (diagnostics.length === failed && diagnostics.every(item => {
        const diagnostic = getRecord(item)
        return diagnostic?.error_type === 'truncated_image' && diagnostic.content_length_matches === false
      })) return 'image_truncated_transfer'
      return 'image_validation_failed'
    }
    return 'image_download_failed'
  }
  // 远程抓取兜底
  if (includesAny(errorCode, ['remotefetcherror', 'remote_fetch_failed']) || includesAny(combined, ['failed to read', 'remote fetch'])) return 'remote_fetch'
  if (includesAny(errorCode, ['unexpectedpageerror', 'unexpected_remote_page']) || includesAny(combined, ['unexpected page', 'expected thread detail page'])) return 'unexpected_page'
  if (includesAny(errorCode, ['invalid_argument', 'valueerror'])) return 'validation'
  return null
}

export function formatJobErrorMessage(
  errorCode: string | null | undefined,
  errorMessage: string | null | undefined,
  lang: 'zh' | 'en',
): string {
  const code = (errorCode || '').trim()
  const message = (errorMessage || '').trim()
  if (!code && !message) return '-'

  if (lang === 'en') {
    const promptMatch = message.match(/expected thread detail page but got prompt page for [^:]+:\s*(.+)$/i)
    if (promptMatch?.[1]) return `Forum returned a notice: ${promptMatch[1].trim()}`
    if (/expected thread detail page but got login_required/i.test(message)) {
      return 'The forum requires login; the current account could not be verified.'
    }
    if (/expected thread detail page but got remote_maintenance/i.test(message)) {
      return 'The forum is under maintenance. This thread is temporarily unavailable.'
    }

    const missingContentMatch = message.match(/floor\s+(\d+)\s+content is required when no images are present/i)
    if (missingContentMatch?.[1]) return `Floor ${missingContentMatch[1]} has no text or images and cannot be archived.`
    if (/content is required when no images are present/i.test(message)) {
      return 'The thread has no text or images and cannot be archived.'
    }
    if (/remote fetch/i.test(message) || code === 'RemoteFetchError') return `Remote fetch failed: ${message}`
    if (/unexpected page/i.test(message) || code === 'UnexpectedPageError') return `Unexpected page received: ${message}`
    if (/login required/i.test(message) || code === 'LoginRequiredError') return `Login is required: ${message}`
    if (/maintenance/i.test(message) || code === 'RemoteMaintenanceError') return `The forum is under maintenance: ${message}`
    if (code && message) return `${code}: ${message}`
    return code || message || '-'
  }

  const promptMatch = message.match(/expected thread detail page but got prompt page for [^:]+:\s*(.+)$/i)
  if (promptMatch?.[1]) {
    return `帖子页面返回了论坛提示：${promptMatch[1].trim()}`
  }

  if (/expected thread detail page but got login_required/i.test(message)) {
    return '帖子页面提示需要登录，当前账号未通过访问校验'
  }

  if (/expected thread detail page but got remote_maintenance/i.test(message)) {
    return '论坛正在维护，帖子暂时无法访问'
  }

  const missingContentMatch = message.match(/floor\s+(\d+)\s+content is required when no images are present/i)
  if (missingContentMatch?.[1]) {
    return `第 ${missingContentMatch[1]} 楼正文为空且没有图片，无法归档`
  }
  if (/content is required when no images are present/i.test(message)) {
    return '正文为空且没有图片，无法归档'
  }

  if (/remote fetch/i.test(message) || code === 'RemoteFetchError') {
    return `远程抓取失败：${message}`
  }
  if (/unexpected page/i.test(message) || code === 'UnexpectedPageError') {
    return `抓取到的页面不符合预期：${message}`
  }
  if (/login required/i.test(message) || code === 'LoginRequiredError') {
    return `需要登录后才能访问：${message}`
  }
  if (/maintenance/i.test(message) || code === 'RemoteMaintenanceError') {
    return `论坛正在维护：${message}`
  }
  if (code && message) return `${code}：${message}`
  return code || message || '-'
}
