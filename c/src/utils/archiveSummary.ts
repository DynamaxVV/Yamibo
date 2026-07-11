import type { ArchiveSummary } from '../api/client'

export type ArchiveBreakdown = {
  downloaded_relpaths?: Record<string, string[]>
  non_export_relpaths?: Record<string, string[]>
  shared_relpaths?: Record<string, string[]>
  skipped_relpaths?: Record<string, string[]>
  missing_image_urls?: string[]
  missing_shared_image_urls?: string[]
}

export function getArchiveBreakdown(summary?: ArchiveSummary | null, artifacts?: Record<string, unknown> | null): ArchiveBreakdown | null {
  const direct = (artifacts?.archive_breakdown as ArchiveBreakdown | undefined) ?? null
  if (direct) return direct
  return summary
    ? {
        downloaded_relpaths: summary.archived_images,
        non_export_relpaths: summary.non_export_images,
        shared_relpaths: summary.shared_images,
        skipped_relpaths: summary.skipped_image_urls,
        missing_image_urls: summary.missing_image_urls,
        missing_shared_image_urls: summary.missing_shared_image_urls,
      }
    : null
}

export function countMapEntries(value?: Record<string, string[]> | null): number {
  return Object.values(value || {}).reduce((total, items) => total + (items?.length || 0), 0)
}

export function hasPartialArchiveBreakdown(breakdown: ArchiveBreakdown | null | undefined): boolean {
  if (!breakdown) return false
  return Boolean(
    (breakdown.missing_image_urls && breakdown.missing_image_urls.length > 0)
    || (breakdown.missing_shared_image_urls && breakdown.missing_shared_image_urls.length > 0),
  )
}

export function getPartialArchiveReason(breakdown: ArchiveBreakdown | null | undefined): string {
  if (!breakdown) return '未知'
  const reasons: string[] = []
  if (breakdown.missing_image_urls?.length) reasons.push(`正文图片缺失 ${breakdown.missing_image_urls.length} 张`)
  if (breakdown.missing_shared_image_urls?.length) reasons.push(`共享资源缺失 ${breakdown.missing_shared_image_urls.length} 个`)
  return reasons.length > 0 ? reasons.join('，') : '无'
}
