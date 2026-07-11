const UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

export function formatBytes(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-'
  if (value === 0) return '0 B'

  let size = Math.max(value, 0)
  let unitIndex = 0
  while (size >= 1024 && unitIndex < UNITS.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  const digits = unitIndex === 0 ? 0 : size >= 100 ? 0 : size >= 10 ? 1 : 2
  return `${size.toFixed(digits)} ${UNITS[unitIndex]}`
}
