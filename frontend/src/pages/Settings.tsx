import { useEffect, useRef, useState } from 'react'
import { Badge } from '../components/Badge'
import { api, type SettingsResponse, type SettingsUpdateResponse } from '../api/client'
import { useI18n } from '../context/I18nContext'

type FieldKind = 'text' | 'password' | 'number' | 'checkbox' | 'textarea' | 'select' | 'tags'
type EffectMode = SettingsResponse['effects'][string]
type Option = { value: string; labelKey: string }

type FieldSpec = {
  key: string
  labelKey: string
  helpKey: string
  kind: FieldKind
  section: string
  placeholderKey?: string
  options?: Option[]
  min?: number
  max?: number
  step?: number
  rows?: number
  inheritable?: boolean
  fullWidth?: boolean
}

type SectionSpec = {
  key: string
  titleKey: string
  descKey: string
  fields: FieldSpec[]
}

const SECTIONS: SectionSpec[] = [
  {
    key: 'archive',
    titleKey: 'settings_section_archive',
    descKey: 'settings_section_archive_desc',
    fields: [
      { key: 'request_timeout_seconds', labelKey: 'settings_request_timeout', helpKey: 'settings_request_timeout_help', kind: 'number', section: 'archive', min: 1, step: 1 },
      { key: 'request_interval_seconds', labelKey: 'settings_request_interval', helpKey: 'settings_request_interval_help', kind: 'number', section: 'archive', min: 0, step: 0.1 },
      { key: 'request_interval_jitter_seconds', labelKey: 'settings_request_jitter', helpKey: 'settings_request_jitter_help', kind: 'number', section: 'archive', min: 0, step: 0.1 },
      { key: 'use_system_proxy', labelKey: 'settings_use_system_proxy', helpKey: 'settings_use_system_proxy_help', kind: 'checkbox', section: 'archive' },
      { key: 'image_download_timeout_seconds', labelKey: 'settings_image_timeout', helpKey: 'settings_image_timeout_help', kind: 'number', section: 'archive', min: 1, step: 1 },
      { key: 'image_download_retries', labelKey: 'settings_image_retries', helpKey: 'settings_image_retries_help', kind: 'number', section: 'archive', min: 0, step: 1 },
      { key: 'archive_thread_max_pages', labelKey: 'settings_archive_pages', helpKey: 'settings_archive_pages_help', kind: 'number', section: 'archive', min: 1, step: 1 },
      { key: 'novel_author_only_max_pages', labelKey: 'settings_novel_pages', helpKey: 'settings_novel_pages_help', kind: 'number', section: 'archive', min: 1, step: 1 },
      { key: 'novel_author_only_page_delay_seconds', labelKey: 'settings_novel_page_delay', helpKey: 'settings_novel_page_delay_help', kind: 'number', section: 'archive', min: 0, step: 0.1 },
    ],
  },
  {
    key: 'title',
    titleKey: 'settings_section_title',
    descKey: 'settings_section_title_desc',
    fields: [
      { key: 'title_parse_use_llm', labelKey: 'settings_title_parse_use_llm', helpKey: 'settings_title_parse_use_llm_help', kind: 'checkbox', section: 'title', fullWidth: true },
      { key: 'common_scanlation_groups', labelKey: 'settings_common_scanlation_groups', helpKey: 'settings_common_scanlation_groups_help', kind: 'tags', section: 'title' },
      { key: 'common_authors', labelKey: 'settings_common_authors', helpKey: 'settings_common_authors_help', kind: 'tags', section: 'title' },
    ],
  },
  {
    key: 'llm',
    titleKey: 'settings_section_llm',
    descKey: 'settings_section_llm_desc',
    fields: [
      { key: 'llm_base_url', labelKey: 'settings_llm_base_url', helpKey: 'settings_llm_base_url_help', kind: 'text', section: 'llm', placeholderKey: 'settings_placeholder_openai_base_url' },
      { key: 'llm_api_key', labelKey: 'settings_llm_api_key', helpKey: 'settings_llm_api_key_help', kind: 'password', section: 'llm', placeholderKey: 'settings_placeholder_llm_api_key' },
      { key: 'llm_model', labelKey: 'settings_llm_model', helpKey: 'settings_llm_model_help', kind: 'select', section: 'llm' },
      { key: 'rag_enabled', labelKey: 'settings_rag_enabled', helpKey: 'settings_rag_enabled_help', kind: 'checkbox', section: 'llm' },
      { key: 'rag_base_url', labelKey: 'settings_rag_base_url', helpKey: 'settings_rag_base_url_help', kind: 'text', section: 'llm', placeholderKey: 'settings_placeholder_inherit_llm', inheritable: true },
      { key: 'rag_api_key', labelKey: 'settings_rag_api_key', helpKey: 'settings_rag_api_key_help', kind: 'password', section: 'llm', placeholderKey: 'settings_placeholder_inherit_llm', inheritable: true },
      { key: 'rag_embedding_model', labelKey: 'settings_rag_embedding_model', helpKey: 'settings_rag_embedding_model_help', kind: 'text', section: 'llm', placeholderKey: 'settings_placeholder_embedding_model' },
      { key: 'rag_embedding_dimensions', labelKey: 'settings_rag_embedding_dimensions', helpKey: 'settings_rag_embedding_dimensions_help', kind: 'number', section: 'llm', min: 1, step: 1 },
    ],
  },
  {
    key: 'export',
    titleKey: 'settings_section_export',
    descKey: 'settings_section_export_desc',
    fields: [
      {
        key: 'export_default_strategy',
        labelKey: 'settings_export_default_strategy',
        helpKey: 'settings_export_default_strategy_help',
        kind: 'select',
        section: 'export',
        options: [
          { value: 'cache_only', labelKey: 'export_strategy_cache_only' },
          { value: 'sync_if_stale', labelKey: 'export_strategy_sync_if_stale' },
          { value: 'force_resync', labelKey: 'export_strategy_force_resync' },
        ],
      },
      { key: 'export_stale_after_hours', labelKey: 'settings_export_stale_after_hours', helpKey: 'settings_export_stale_after_hours_help', kind: 'number', section: 'export', min: 1, step: 1 },
      { key: 'novel_txt_include_filtered_notes', labelKey: 'settings_novel_txt_include_filtered_notes', helpKey: 'settings_novel_txt_include_filtered_notes_help', kind: 'checkbox', section: 'export' },
      { key: 'novel_txt_debug_markers', labelKey: 'settings_novel_txt_debug_markers', helpKey: 'settings_novel_txt_debug_markers_help', kind: 'checkbox', section: 'export' },
    ],
  },
  {
    key: 'maintenance',
    titleKey: 'settings_section_maintenance',
    descKey: 'settings_section_maintenance_desc',
    fields: [
      { key: 'jobs_enabled', labelKey: 'settings_jobs_enabled', helpKey: 'settings_jobs_enabled_help', kind: 'checkbox', section: 'maintenance' },
      { key: 'backup_keep_count', labelKey: 'settings_backup_keep_count', helpKey: 'settings_backup_keep_count_help', kind: 'number', section: 'maintenance', min: 1, step: 1 },
      { key: 'cleanup_staging_older_than_hours', labelKey: 'settings_cleanup_staging_older_than_hours', helpKey: 'settings_cleanup_staging_older_than_hours_help', kind: 'number', section: 'maintenance', min: 1, step: 1 },
      { key: 'worker_parallelism', labelKey: 'settings_worker_parallelism', helpKey: 'settings_worker_parallelism_help', kind: 'number', section: 'maintenance', min: 1, step: 1 },
      { key: 'worker_poll_seconds', labelKey: 'settings_worker_poll_seconds', helpKey: 'settings_worker_poll_seconds_help', kind: 'number', section: 'maintenance', min: 0.1, step: 0.1 },
      { key: 'worker_lease_seconds', labelKey: 'settings_worker_lease_seconds', helpKey: 'settings_worker_lease_seconds_help', kind: 'number', section: 'maintenance', min: 1, step: 1 },
      { key: 'worker_heartbeat_seconds', labelKey: 'settings_worker_heartbeat_seconds', helpKey: 'settings_worker_heartbeat_seconds_help', kind: 'number', section: 'maintenance', min: 1, step: 1 },
    ],
  },
  {
    key: 'advanced',
    titleKey: 'settings_section_advanced',
    descKey: 'settings_section_advanced_desc',
    fields: [
      { key: 'rag_chunker_version', labelKey: 'settings_rag_chunker_version', helpKey: 'settings_rag_chunker_version_help', kind: 'text', section: 'advanced', placeholderKey: 'settings_placeholder_chunker_version' },
      { key: 'rag_min_chunk_chars', labelKey: 'settings_rag_min_chunk_chars', helpKey: 'settings_rag_min_chunk_chars_help', kind: 'number', section: 'advanced', min: 1, step: 1 },
      { key: 'rag_max_chunk_chars', labelKey: 'settings_rag_max_chunk_chars', helpKey: 'settings_rag_max_chunk_chars_help', kind: 'number', section: 'advanced', min: 1, step: 1 },
      { key: 'rag_hybrid_fts_candidates', labelKey: 'settings_rag_hybrid_fts_candidates', helpKey: 'settings_rag_hybrid_fts_candidates_help', kind: 'number', section: 'advanced', min: 1, step: 1 },
      { key: 'rag_hybrid_vector_candidates', labelKey: 'settings_rag_hybrid_vector_candidates', helpKey: 'settings_rag_hybrid_vector_candidates_help', kind: 'number', section: 'advanced', min: 1, step: 1 },
      { key: 'rag_debug_indexing', labelKey: 'settings_rag_debug_indexing', helpKey: 'settings_rag_debug_indexing_help', kind: 'checkbox', section: 'advanced' },
    ],
  },
]

const ALL_FIELDS = SECTIONS.flatMap(section => section.fields)
const VISIBLE_SECTIONS = SECTIONS.filter(section => section.key !== 'advanced')
const ADVANCED_SECTION = SECTIONS.find(section => section.key === 'advanced')!

function valueToInput(field: FieldSpec, payload: SettingsResponse | null) {
  if (!payload) return ''
  const source = payload.sources[field.key]
  const locked = payload.locked_fields.includes(field.key)
  const stored = payload.stored[field.key]
  const effective = payload.values[field.key]
  const raw = field.inheritable && source === 'derived' && !locked ? stored : (stored ?? effective)
  if (field.kind === 'checkbox') return raw ? '1' : '0'
  if (field.kind === 'textarea' || field.kind === 'tags') return Array.isArray(raw) ? raw.join('\n') : String(raw ?? '')
  return raw == null ? '' : String(raw)
}

function sourceLabel(t: (key: string, vars?: Record<string, string | number>) => string, source: string) {
  switch (source) {
    case 'env':
      return t('settings_source_env')
    case 'file':
      return t('settings_source_file')
    case 'derived':
      return t('settings_source_derived')
    default:
      return t('settings_source_default')
  }
}

function formatListInput(value: string) {
  return value.split('\n').map(line => line.trim()).filter(Boolean)
}

function TagEditor({ value, disabled, onChange }: { value: string; disabled: boolean; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState('')
  const tags = formatListInput(value)

  const add = (raw: string) => {
    const name = raw.trim()
    if (!name) return
    if (tags.includes(name)) { setDraft(''); return }
    onChange([...tags, name].join('\n'))
    setDraft('')
  }

  const remove = (name: string) => {
    onChange(tags.filter(t => t !== name).join('\n'))
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); add(draft) }
    if (e.key === 'Backspace' && !draft && tags.length) remove(tags[tags.length - 1])
  }

  return (
    <div className={`tag-editor ${disabled ? 'tag-editor-disabled' : ''}`}>
      {tags.map(tag => (
        <span key={tag} className="tag-chip">
          {tag}
          {!disabled && <button type="button" className="tag-chip-x" onClick={() => remove(tag)} aria-label={`Remove ${tag}`}>&times;</button>}
        </span>
      ))}
      {!disabled && (
        <input
          className="tag-editor-input"
          value={draft}
          placeholder={tags.length ? '' : '输入后回车添加...'}
          onChange={e => { setDraft(e.target.value) }}
          onKeyDown={handleKey}
          onBlur={() => { if (draft.trim()) add(draft) }}
        />
      )}
    </div>
  )
}

function effectLabelKey(mode: EffectMode) {
  switch (mode) {
    case 'restart_daemon':
      return 'settings_effect_restart_daemon'
    case 'restart_web':
      return 'settings_effect_restart_web'
    case 'restart_daemon_web':
      return 'settings_effect_restart_daemon_web'
    default:
      return 'settings_effect_immediate'
  }
}

function effectBadgeStatus(mode: EffectMode) {
  switch (mode) {
    case 'immediate':
      return 'ok'
    default:
      return 'warn'
  }
}

function sectionSummaryKey(section: SectionSpec, payload: SettingsResponse) {
  const modes = Array.from(new Set(section.fields.map(field => payload.effects[field.key] || 'immediate')))
  return modes.length <= 1 ? effectLabelKey(modes[0] || 'immediate') : 'settings_effect_mixed'
}

function changedFieldsForSection(section: SectionSpec, payload: SettingsResponse, formValues: Record<string, string>) {
  return section.fields.filter(field => {
    if (payload.locked_fields.includes(field.key)) return false
    return (formValues[field.key] || '') !== valueToInput(field, payload)
  })
}

function parseFieldValue(
  field: FieldSpec,
  raw: string,
  t: (key: string, vars?: Record<string, string | number>) => string,
) {
  if (field.kind === 'checkbox') return raw === '1'
  if (field.kind === 'textarea' || field.kind === 'tags') return formatListInput(raw)
  if (field.kind === 'number') {
    if (!raw.trim()) throw new Error(t('settings_required_value', { label: t(field.labelKey) }))
    const parsed = field.step && field.step < 1 ? Number.parseFloat(raw) : Number.parseInt(raw, 10)
    if (Number.isNaN(parsed)) throw new Error(t('settings_invalid_number', { label: t(field.labelKey) }))
    return parsed
  }
  if (field.kind === 'select') {
    const trimmed = raw.trim()
    if (!trimmed) throw new Error(t('settings_required_value', { label: t(field.labelKey) }))
    return trimmed
  }
  const trimmed = raw.trim()
  if (!trimmed && !field.inheritable) throw new Error(t('settings_required_value', { label: t(field.labelKey) }))
  return trimmed
}

function restartTargetsLabel(
  summary: Pick<SettingsUpdateResponse, 'effect_mode_summary' | 'restart_targets'>,
  t: (key: string, vars?: Record<string, string | number>) => string,
) {
  if (summary.effect_mode_summary === 'restart_daemon_web') return t('settings_effect_restart_daemon_web')
  if (summary.effect_mode_summary === 'restart_web') return t('settings_effect_restart_web')
  if (summary.effect_mode_summary === 'restart_daemon') return t('settings_effect_restart_daemon')
  if (summary.restart_targets.includes('daemon') && summary.restart_targets.includes('web')) return t('settings_effect_restart_daemon_web')
  if (summary.restart_targets.includes('web')) return t('settings_effect_restart_web')
  if (summary.restart_targets.includes('daemon')) return t('settings_effect_restart_daemon')
  return t('settings_effect_immediate')
}

function savedMessage(
  summary: Pick<SettingsUpdateResponse, 'effect_mode_summary' | 'restart_targets'>,
  t: (key: string, vars?: Record<string, string | number>) => string,
) {
  switch (summary.effect_mode_summary) {
    case 'restart_daemon':
      return t('settings_saved_restart_daemon')
    case 'restart_web':
      return t('settings_saved_restart_web')
    case 'restart_daemon_web':
      return t('settings_saved_restart_daemon_web')
    default:
      return t('settings_saved_immediate')
  }
}

function SettingsSection({
  section,
  payload,
  formValues,
  setFormValues,
  saving,
  saveSection,
  renderModelSelect,
  modelError,
}: {
  section: SectionSpec
  payload: SettingsResponse
  formValues: Record<string, string>
  setFormValues: React.Dispatch<React.SetStateAction<Record<string, string>>>
  saving: boolean
  saveSection: (section: SectionSpec) => Promise<void>
  renderModelSelect: (field: FieldSpec, locked: boolean) => JSX.Element
  modelError: string | null
}) {
  const { t } = useI18n()

  return (
    <section className="panel settings-section">
      <div className="settings-section-head">
        <div>
          <div className="settings-section-title-row">
            <h3 className="settings-section-title">{t(section.titleKey)}</h3>
            <Badge status={sectionSummaryKey(section, payload) === 'settings_effect_immediate' ? 'ok' : 'warn'}>
              {t(sectionSummaryKey(section, payload))}
            </Badge>
          </div>
          <p className="settings-copy">{t(section.descKey)}</p>
        </div>
        <button type="button" className="btn-primary settings-section-save" onClick={() => void saveSection(section)} disabled={saving}>
          {saving ? t('running') : t('save')}
        </button>
      </div>
      <div className="settings-grid">
        {section.fields.map(field => {
          const locked = payload.locked_fields.includes(field.key)
          const source = payload.sources[field.key] || 'default'
          const effect = payload.effects[field.key] || 'immediate'
          return (
            <div key={field.key} className={`settings-field settings-field-${field.kind}`} style={field.fullWidth ? { gridColumn: '1 / -1' } : undefined}>
              <div className="settings-field-head">
                <span className="settings-field-label">{t(field.labelKey)}</span>
                <div className="settings-field-badges">
                  <Badge status={effectBadgeStatus(effect)}>{t(effectLabelKey(effect))}</Badge>
                  {locked && <Badge status="warn">{t('settings_locked')}</Badge>}
                </div>
              </div>
              {field.kind === 'tags' ? (
                <TagEditor
                  value={formValues[field.key] || ''}
                  disabled={locked}
                  onChange={v => setFormValues(prev => ({ ...prev, [field.key]: v }))}
                />
              ) : field.kind === 'textarea' ? (
                <textarea
                  rows={field.rows || 4}
                  value={formValues[field.key] || ''}
                  disabled={locked}
                  placeholder={field.placeholderKey ? t(field.placeholderKey) : undefined}
                  onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
                />
              ) : field.kind === 'select' ? (
                field.key === 'llm_model'
                  ? renderModelSelect(field, locked)
                  : (
                    <select
                      value={formValues[field.key] || ''}
                      disabled={locked}
                      onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
                    >
                      {(field.options || []).map(option => (
                        <option key={option.value} value={option.value}>{t(option.labelKey)}</option>
                      ))}
                    </select>
                  )
              ) : field.kind === 'checkbox' ? (
                <label className="settings-check">
                  <input
                    type="checkbox"
                    checked={(formValues[field.key] || '0') === '1'}
                    disabled={locked}
                    onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.checked ? '1' : '0' }))}
                  />
                  <span>{locked ? t('settings_locked_edit') : t('settings_toggle_hint')}</span>
                </label>
              ) : (
                <input
                  type={field.kind === 'password' ? 'password' : field.kind === 'number' ? 'number' : 'text'}
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  value={formValues[field.key] || ''}
                  disabled={locked}
                  placeholder={field.placeholderKey ? t(field.placeholderKey) : undefined}
                  onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
                />
              )}
              <small className="settings-help">
                {t(field.helpKey)}
                {field.inheritable && source === 'derived' && ` · ${t('settings_inherit_from_llm')}`}
              </small>
              <small className="settings-source">
                {t('settings_current_source')}: {sourceLabel(t, source)}
              </small>
              {field.key === 'llm_model' && modelError && <small className="settings-source settings-source-error">{modelError}</small>}
            </div>
          )
        })}
      </div>
    </section>
  )
}

export function Settings() {
  const { t } = useI18n()
  const [payload, setPayload] = useState<SettingsResponse | null>(null)
  const [formValues, setFormValues] = useState<Record<string, string>>({})
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [modelLoading, setModelLoading] = useState(false)
  const [modelLoaded, setModelLoaded] = useState(false)
  const [modelError, setModelError] = useState<string | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const modelLoadLock = useRef(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    api.settings().then(next => {
      if (!active) return
      setPayload(next)
      const initial: Record<string, string> = {}
      ALL_FIELDS.forEach(field => {
        initial[field.key] = valueToInput(field, next)
      })
      setFormValues(initial)
      setLoading(false)
    }).catch((e: Error) => {
      if (!active) return
      setError(e.message)
      setLoading(false)
    })
    return () => { active = false }
  }, [])

  const loadModels = async () => {
    if (modelLoadLock.current || modelLoaded) return
    modelLoadLock.current = true
    setModelLoading(true)
    setModelError(null)
    try {
      const result = await api.settingsModels()
      setModelOptions(result.models)
      setModelLoaded(true)
    } catch (e: any) {
      setModelError(e?.message || String(e))
    } finally {
      modelLoadLock.current = false
      setModelLoading(false)
    }
  }

  const currentModels = (() => {
    const current = formValues.llm_model || ''
    return Array.from(new Set([current, ...modelOptions].filter(Boolean)))
  })()

  const renderModelSelect = (field: FieldSpec, locked: boolean) => (
    <div className="settings-select-wrap">
      <select
        value={formValues[field.key] || ''}
        disabled={locked}
        onMouseDown={() => { void loadModels() }}
        onFocus={() => { void loadModels() }}
        onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
      >
        {modelLoading ? (
          <option value="">{t('settings_models_loading')}</option>
        ) : currentModels.length > 0 ? (
          currentModels.map(option => (
            <option key={option} value={option}>{option}</option>
          ))
        ) : (
          <option value="">{t('settings_models_empty')}</option>
        )}
      </select>
      <button type="button" className="btn-subtle settings-model-refresh" onClick={() => void loadModels()} disabled={locked || modelLoading}>
        {modelLoading ? t('loading') : t('settings_models_refresh')}
      </button>
    </div>
  )

  const refresh = async () => {
    const next = await api.settings()
    setPayload(next)
    const initial: Record<string, string> = {}
    ALL_FIELDS.forEach(field => {
      initial[field.key] = valueToInput(field, next)
    })
    setFormValues(initial)
  }

  const saveSection = async (section: SectionSpec) => {
    if (!payload) return
    const changedFields = changedFieldsForSection(section, payload, formValues)
    if (changedFields.length === 0) {
      setMessage(t('settings_no_changes'))
      setError(null)
      return
    }

    const changedEffects = changedFields.map(field => payload.effects[field.key] || 'immediate')
    const requiresRestart = changedEffects.some(mode => mode !== 'immediate')
    if (requiresRestart) {
      const restartTargets = Array.from(new Set(changedEffects.flatMap(mode => {
        if (mode === 'restart_daemon_web') return ['daemon', 'web']
        if (mode === 'restart_daemon') return ['daemon']
        if (mode === 'restart_web') return ['web']
        return []
      })))
      const confirmed = window.confirm(t('settings_restart_required_prompt', {
        section: t(section.titleKey),
        targets: restartTargetsLabel(
          {
            effect_mode_summary: restartTargets.includes('daemon') && restartTargets.includes('web')
              ? 'restart_daemon_web'
              : restartTargets.includes('web')
                ? 'restart_web'
                : 'restart_daemon',
            restart_targets: restartTargets as Array<'daemon' | 'web'>,
          },
          t,
        ),
      }))
      if (!confirmed) return
    }

    setSaving(true)
    setError(null)
    setMessage(null)
    try {
      const values: Record<string, unknown> = {}
      for (const field of section.fields) {
        if (payload.locked_fields.includes(field.key)) continue
        values[field.key] = parseFieldValue(field, formValues[field.key] ?? '', t)
      }
      const next = await api.updateSettings(values)
      setPayload(next)
      setFormValues(current => {
        const nextValues = { ...current }
        section.fields.forEach(field => {
          nextValues[field.key] = valueToInput(field, next)
        })
        return nextValues
      })
      setMessage(savedMessage(next, t))
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="panel">{t('loading')}</div>
  if (!payload) return <div className="panel">{error || t('error')}</div>

  return (
    <div className="settings-page">
      <div className="settings-form">
        <section className="panel settings-hero">
          <div>
            <h2 className="settings-title">{t('settings_title')}</h2>
            <p className="settings-copy">{t('settings_desc')}</p>
          </div>
          <div className="settings-hero-actions">
            <button type="button" className="btn-subtle" onClick={() => void refresh()} disabled={saving}>{t('refresh')}</button>
          </div>
          <div className="settings-legend">
            <p className="settings-legend-copy">{t('settings_legend_desc')}</p>
            <div className="settings-legend-items">
              <Badge status="ok">{t('settings_effect_immediate')}</Badge>
              <Badge status="warn">{t('settings_effect_restart_daemon')}</Badge>
              <Badge status="warn">{t('settings_effect_restart_web')}</Badge>
              <Badge status="warn">{t('settings_effect_restart_daemon_web')}</Badge>
            </div>
          </div>
          {payload.locked_fields.length > 0 && (
            <div className="settings-warning">
              <Badge status="warn">{t('settings_env_locked')}</Badge>
              <span>{t('settings_env_locked_desc', { count: payload.locked_fields.length })}</span>
            </div>
          )}
          {message && <div className="settings-note settings-note-ok">{message}</div>}
          {error && <div className="settings-note settings-note-error">{error}</div>}
        </section>

        {VISIBLE_SECTIONS.map(section => (
          <SettingsSection
            key={section.key}
            section={section}
            payload={payload}
            formValues={formValues}
            setFormValues={setFormValues}
            saving={saving}
            saveSection={saveSection}
            renderModelSelect={renderModelSelect}
            modelError={modelError}
          />
        ))}

        <section className="panel settings-footer">
          <div className="settings-footer-copy">{t('settings_footer_note')}</div>
        </section>

        <details className="panel settings-section settings-section-advanced" open={advancedOpen} onToggle={e => setAdvancedOpen((e.currentTarget as HTMLDetailsElement).open)}>
          <summary className="settings-advanced-summary">
            <div className="settings-section-head">
              <div>
                <div className="settings-section-title-row">
                  <h3 className="settings-section-title">{t('settings_section_advanced')}</h3>
                  <Badge status={sectionSummaryKey(ADVANCED_SECTION, payload) === 'settings_effect_immediate' ? 'ok' : 'warn'}>
                    {t(sectionSummaryKey(ADVANCED_SECTION, payload))}
                  </Badge>
                </div>
                <p className="settings-copy">{t('settings_section_advanced_desc')}</p>
              </div>
              <button type="button" className="btn-primary settings-section-save" onClick={(e) => { e.preventDefault(); void saveSection(ADVANCED_SECTION) }} disabled={saving}>
                {saving ? t('running') : t('save')}
              </button>
            </div>
          </summary>
          <div className="settings-grid">
            {ADVANCED_SECTION.fields.map(field => {
              const locked = payload.locked_fields.includes(field.key)
              const source = payload.sources[field.key] || 'default'
              const effect = payload.effects[field.key] || 'immediate'
              return (
                <div key={field.key} className={`settings-field settings-field-${field.kind}`} style={field.fullWidth ? { gridColumn: '1 / -1' } : undefined}>
                  <div className="settings-field-head">
                    <span className="settings-field-label">{t(field.labelKey)}</span>
                    <div className="settings-field-badges">
                      <Badge status={effectBadgeStatus(effect)}>{t(effectLabelKey(effect))}</Badge>
                      {locked && <Badge status="warn">{t('settings_locked')}</Badge>}
                    </div>
                  </div>
                  {field.kind === 'checkbox' ? (
                    <label className="settings-check">
                      <input
                        type="checkbox"
                        checked={(formValues[field.key] || '0') === '1'}
                        disabled={locked}
                        onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.checked ? '1' : '0' }))}
                      />
                      <span>{locked ? t('settings_locked_edit') : t('settings_toggle_hint')}</span>
                    </label>
                  ) : field.kind === 'number' ? (
                    <input
                      type="number"
                      min={field.min}
                      max={field.max}
                      step={field.step}
                      value={formValues[field.key] || ''}
                      disabled={locked}
                      placeholder={field.placeholderKey ? t(field.placeholderKey) : undefined}
                      onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
                    />
                  ) : (
                    <input
                      type={field.kind === 'password' ? 'password' : 'text'}
                      value={formValues[field.key] || ''}
                      disabled={locked}
                      placeholder={field.placeholderKey ? t(field.placeholderKey) : undefined}
                      onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
                    />
                  )}
                  <small className="settings-help">
                    {t(field.helpKey)}
                    {field.inheritable && source === 'derived' && ` · ${t('settings_inherit_from_llm')}`}
                  </small>
                  <small className="settings-source">
                    {t('settings_current_source')}: {sourceLabel(t, source)}
                  </small>
                </div>
              )
            })}
          </div>
        </details>
      </div>
    </div>
  )
}
