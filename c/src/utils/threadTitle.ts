type ThreadTitleLike = {
  raw_title?: string | null
  display_title?: string | null
  title?: string | null
  content_kind?: string | null
  core_title_guess?: string | null
  chapter_name?: string | null
  author_guess?: string | null
  group_name?: string | null
}

const TITLE_PREFIX_RE = /^\s*(?:【([^】]*)】|\[([^\]]*)\]|［([^］]*)］|\(([^)]*)\)|（([^）]*)）)\s*/

function stripAuthorAndGroupPrefixes(title: string, author: string, group: string): string {
  const removable = new Set([author, group].filter(Boolean))
  let rest = title
  let preservedPrefixes = ''

  while (true) {
    const match = TITLE_PREFIX_RE.exec(rest)
    if (!match) break
    const prefix = match[0]
    const label = match.slice(1).find(value => value !== undefined)?.trim() || ''
    if (removable.has(label)) rest = rest.slice(prefix.length)
    else preservedPrefixes += prefix
    if (!removable.has(label)) rest = rest.slice(prefix.length)
  }

  return `${preservedPrefixes}${rest}`.replace(/\s+/g, ' ').trim()
}

export function formatThreadListTitle(thread: ThreadTitleLike): string {
  const fallback = (thread.display_title || thread.title || thread.raw_title || '').trim()
  if (thread.content_kind !== 'comic') {
    return fallback
  }
  const author = (thread.author_guess || '').trim()
  const group = (thread.group_name || '').trim()
  const originalBody = stripAuthorAndGroupPrefixes(fallback, author, group)
  if (!originalBody) return fallback
  return `${author ? `[${author}]` : ''}${originalBody}${group ? `【${group}】` : ''}`
}
