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

export function formatThreadListTitle(thread: ThreadTitleLike): string {
  const fallback = (thread.display_title || thread.title || thread.raw_title || '').trim()
  if (thread.content_kind !== 'comic') {
    return fallback
  }
  const seriesName = (thread.core_title_guess || '').trim()
  const chapterName = (thread.chapter_name || '').trim()
  const author = (thread.author_guess || '').trim()
  const group = (thread.group_name || '').trim()
  const middle = [seriesName, chapterName].filter(Boolean).join(' ').trim()
  if (!middle) {
    return fallback
  }
  return `${author ? `[${author}]` : ''}${middle}${group ? `【${group}】` : ''}`
}
