export function formatDateTime(iso: string | null): string {
  if (!iso) return '-'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    const utc8 = new Date(d.getTime() + 8 * 60 * 60 * 1000)
    const y = utc8.getUTCFullYear()
    const m = String(utc8.getUTCMonth() + 1).padStart(2, '0')
    const day = String(utc8.getUTCDate()).padStart(2, '0')
    const h = String(utc8.getUTCHours()).padStart(2, '0')
    const min = String(utc8.getUTCMinutes()).padStart(2, '0')
    const s = String(utc8.getUTCSeconds()).padStart(2, '0')
    return `${y}-${m}-${day} ${h}:${min}:${s}`
  } catch {
    return iso
  }
}

export function formatLogTime(ts: number): string {
  const d = new Date(ts * 1000)
  const utc8 = new Date(d.getTime() + 8 * 60 * 60 * 1000)
  const h = String(utc8.getUTCHours()).padStart(2, '0')
  const min = String(utc8.getUTCMinutes()).padStart(2, '0')
  const s = String(utc8.getUTCSeconds()).padStart(2, '0')
  const ms = String(d.getMilliseconds()).padStart(3, '0')
  return `${h}:${min}:${s}.${ms}`
}
