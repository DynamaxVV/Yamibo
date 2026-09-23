import { useEffect, useMemo, useState } from 'react'
import { api, type TableLayout, type TableLayouts, type SettingsUpdateResponse } from '../api/client'
import { useI18n } from '../context/I18nContext'

type TableName = keyof TableLayouts
type ColumnDefinition = { key: string; labelKey: string; required?: boolean }

const DEFINITIONS: Record<TableName, ColumnDefinition[]> = {
  threads: [
    { key: 'tid', labelKey: 'tid' }, { key: 'title', labelKey: 'title' }, { key: 'forum', labelKey: 'forum' },
    { key: 'category', labelKey: 'category' }, { key: 'archive', labelKey: 'archive' }, { key: 'reply_count', labelKey: 'reply_count' },
    { key: 'pub_time', labelKey: 'pub_time' }, { key: 'last_reply_time', labelKey: 'last_reply_time' }, { key: 'sync_time', labelKey: 'sync_time' },
    { key: 'action', labelKey: 'action', required: true },
  ],
  jobs: [
    { key: 'tid', labelKey: 'tid' }, { key: 'description', labelKey: 'description' }, { key: 'status', labelKey: 'status' },
    { key: 'stage', labelKey: 'stage' }, { key: 'progress', labelKey: 'progress' }, { key: 'created_at', labelKey: 'created_at' },
    { key: 'action', labelKey: 'action', required: true },
  ],
}

const DEFAULTS: TableLayouts = {
  threads: DEFINITIONS.threads.map((_, i) => ({ key: DEFINITIONS.threads[i].key, visible: !['category', 'last_reply_time', 'sync_time'].includes(DEFINITIONS.threads[i].key), width: [60, 480, 100, 110, 80, 65, 105, 105, 105, 84][i] })),
  jobs: DEFINITIONS.jobs.map((_, i) => ({ key: DEFINITIONS.jobs[i].key, visible: DEFINITIONS.jobs[i].key !== 'stage', width: [65, 420, 80, 120, 80, 110, 110][i] })),
}

const TABLE_LAYOUT_STORAGE_KEY = 'yamibo_table_layouts'

function readCachedLayouts(): unknown {
  try {
    const raw = window.localStorage.getItem(TABLE_LAYOUT_STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function cacheLayouts(value: unknown) {
  try {
    window.localStorage.setItem(TABLE_LAYOUT_STORAGE_KEY, JSON.stringify(normalizeLayouts(value)))
  } catch {
    // localStorage may be unavailable; the server remains the source of truth.
  }
}

function normalizeLayouts(value: unknown): TableLayouts {
  const raw = value as Partial<TableLayouts> | null
  return (['threads', 'jobs'] as TableName[]).reduce((result, table) => {
    const entries = Array.isArray(raw?.[table]) ? raw[table] : []
    const byKey = new Map(entries.map(item => [item.key, item]))
    result[table] = DEFINITIONS[table].map(def => {
      const recommended = DEFAULTS[table].find(item => item.key === def.key)!
      const saved = byKey.get(def.key)
      return { ...recommended, ...saved, key: def.key, visible: def.required || Boolean(saved?.visible ?? recommended.visible) }
    })
    return result
  }, {} as TableLayouts)
}

export function getTableLayout(value: unknown, table: TableName): TableLayout[] {
  return normalizeLayouts(value)[table]
}

export function useTableLayout(table: TableName) {
  const [layouts, setLayouts] = useState<TableLayout[]>(() => getTableLayout(readCachedLayouts(), table))
  useEffect(() => {
    api.settingsLayouts().then(result => {
      cacheLayouts(result.table_layouts)
      setLayouts(getTableLayout(result.table_layouts, table))
    }).catch(() => {})
  }, [table])
  return layouts
}

export function TableLayoutEditor({ value, revision, onSaved, onDirtyChange, onAuthRequired }: {
  value: unknown
  revision?: string
  onSaved: (result: SettingsUpdateResponse) => void
  onDirtyChange?: (dirty: boolean) => void
  onAuthRequired?: (error: Error) => void
}) {
  const { t, tx } = useI18n()
  const [layouts, setLayouts] = useState<TableLayouts>(() => normalizeLayouts(value))
  const [table, setTable] = useState<TableName>('threads')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [messageIsError, setMessageIsError] = useState(false)
  const savedLayoutJson = JSON.stringify(normalizeLayouts(value))

  useEffect(() => {
    const normalized = JSON.parse(savedLayoutJson) as TableLayouts
    cacheLayouts(normalized)
    setLayouts(normalized)
  }, [savedLayoutJson])
  useEffect(() => {
    onDirtyChange?.(JSON.stringify(layouts) !== savedLayoutJson)
  }, [layouts, savedLayoutJson, onDirtyChange])
  const definitions = DEFINITIONS[table]
  const current = layouts[table]
  const ordered = useMemo(() => current.map(item => definitions.find(def => def.key === item.key)!).filter(Boolean), [current, definitions])

  const updateTable = (next: TableLayout[]) => setLayouts(prev => ({ ...prev, [table]: next }))
  const save = async () => {
    setSaving(true)
    setMessage(null)
    setMessageIsError(false)
    try {
      const result = await api.updateSettings({ table_layouts: layouts }, revision)
      cacheLayouts(result.values.table_layouts)
      onSaved(result)
      setMessage(t('settings_table_layout_saved'))
    } catch (error: any) {
      setMessageIsError(true)
      setMessage(error?.message || String(error))
      if (error?.code === 'SETTINGS_AUTH_REQUIRED') onAuthRequired?.(error)
    } finally { setSaving(false) }
  }

  return (
    <section className="panel settings-section">
      <div className="settings-section-head">
        <div><h3 className="settings-section-title">{t('settings_table_layout_title')}</h3><p className="settings-copy">{t('settings_table_layout_desc')}</p></div>
        <button type="button" className="btn-primary settings-section-save" onClick={() => void save()} disabled={saving}>{saving ? t('running') : t('save')}</button>
      </div>
      <div className="table-layout-tabs">
        <button type="button" className={table === 'threads' ? 'active' : ''} onClick={() => setTable('threads')}>{t('settings_table_threads')}</button>
        <button type="button" className={table === 'jobs' ? 'active' : ''} onClick={() => setTable('jobs')}>{t('settings_table_jobs')}</button>
      </div>
      <button type="button" className="btn-secondary" onClick={() => updateTable(DEFAULTS[table].map(item => ({ ...item })))}>{tx('恢复推荐布局', 'Restore recommended layout')}</button>
      <div className="table-layout-editor">
        <div className="table-layout-list">
          {ordered.map(def => {
            const item = current.find(entry => entry.key === def.key)!
            return <div key={def.key} className="table-layout-row">
              <label className="table-layout-visible"><input type="checkbox" checked={def.required || item.visible} disabled={def.required} onChange={e => updateTable(current.map(entry => entry.key === def.key ? { ...entry, visible: e.target.checked } : entry))} />{t(def.labelKey)}</label>
              <label className="table-layout-width"><span>{t('settings_table_width')}</span><input type="number" min={def.key === 'title' || def.key === 'description' ? 320 : 32} max="800" step="1" value={item.width} onChange={e => updateTable(current.map(entry => entry.key === def.key ? { ...entry, width: Math.max(def.key === 'title' || def.key === 'description' ? 320 : 32, Number(e.target.value) || 32) } : entry))} /><span>px</span></label>
            </div>
          })}
        </div>
        <div className="table-layout-preview"><div className="table-layout-preview-label">{t('settings_table_preview')}</div><div className="table-layout-preview-table">{current.filter(item => item.visible).map(item => <span key={item.key} style={{ width: `${Math.min(item.width, 240)}px` }}>{t(definitions.find(def => def.key === item.key)!.labelKey)}</span>)}</div></div>
      </div>
      {message && <div role={messageIsError ? 'alert' : 'status'} className={`settings-note ${messageIsError ? 'settings-note-error' : 'settings-note-ok'}`}>{message}</div>}
    </section>
  )
}
