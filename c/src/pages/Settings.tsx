import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '../components/Badge'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { api, type ScheduledArchiveTask, type ScheduledArchiveRun, type ScheduledArchiveInput, type SkillReviewProposal, type SettingsResponse, type SettingsUpdateResponse } from '../api/client'
import { useI18n } from '../context/I18nContext'
import { TableLayoutEditor } from '../components/TableLayoutEditor'

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
    key: 'embedded', titleKey: 'settings_embedded_title', descKey: 'settings_embedded_desc',
    fields: [
      { key: 'chat_backend', labelKey: 'settings_chat_backend', helpKey: 'settings_chat_backend_help', kind: 'select', section: 'embedded', options: [{ value: 'hermes', labelKey: 'settings_backend_hermes' }, { value: 'embedded', labelKey: 'settings_backend_embedded' }] },
      { key: 'chat_timeout', labelKey: 'settings_chat_timeout', helpKey: 'settings_chat_timeout_help', kind: 'number', section: 'embedded', min: 1 },
    ],
  },
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
      { key: 'common_scanlation_groups', labelKey: 'settings_common_scanlation_groups', helpKey: 'settings_common_scanlation_groups_help', kind: 'tags', section: 'title' },
      { key: 'common_authors', labelKey: 'settings_common_authors', helpKey: 'settings_common_authors_help', kind: 'tags', section: 'title' },
    ],
  },
  {
    key: 'hermes',
    titleKey: 'settings_section_hermes',
    descKey: 'settings_section_hermes_desc',
    fields: [
      { key: 'hermes_host', labelKey: 'settings_hermes_host', helpKey: 'settings_hermes_host_help', kind: 'text', section: 'hermes', placeholderKey: 'settings_placeholder_hermes_host' },
      { key: 'hermes_port', labelKey: 'settings_hermes_port', helpKey: 'settings_hermes_port_help', kind: 'number', section: 'hermes', min: 1, max: 65535, step: 1 },
      { key: 'hermes_model', labelKey: 'settings_hermes_model', helpKey: 'settings_hermes_model_help', kind: 'text', section: 'hermes', placeholderKey: 'settings_placeholder_hermes_model' },
      { key: 'hermes_api_key', labelKey: 'settings_hermes_api_key', helpKey: 'settings_hermes_api_key_help', kind: 'password', section: 'hermes', placeholderKey: 'settings_placeholder_hermes_api_key' },
      { key: 'chat_streaming_enabled', labelKey: 'settings_chat_streaming_enabled', helpKey: 'settings_chat_streaming_enabled_help', kind: 'checkbox', section: 'hermes', fullWidth: true },
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
      { key: 'rag_hybrid_fts_candidates', labelKey: 'settings_rag_hybrid_fts_candidates', helpKey: 'settings_rag_hybrid_fts_candidates_help', kind: 'number', section: 'advanced', min: 1, step: 1 },
      { key: 'rag_hybrid_vector_candidates', labelKey: 'settings_rag_hybrid_vector_candidates', helpKey: 'settings_rag_hybrid_vector_candidates_help', kind: 'number', section: 'advanced', min: 1, step: 1 },
      { key: 'rag_debug_indexing', labelKey: 'settings_rag_debug_indexing', helpKey: 'settings_rag_debug_indexing_help', kind: 'checkbox', section: 'advanced' },
    ],
  },
]

const ORIGINAL_FIELDS = SECTIONS.flatMap(section => section.fields)
const extraField = (key: string, kind: FieldKind = 'number', min = 1): FieldSpec => ({
  key, kind, min, step: 1, labelKey: 'settings_' + key, helpKey: 'settings_' + key + '_help', section: '',
})
const ALL_FIELDS = [
  ...ORIGINAL_FIELDS,
  { ...extraField('title_parse_mode', 'select'), options: [
    { value: 'rules_only', labelKey: 'settings_title_rules_only' },
    { value: 'fallback', labelKey: 'settings_title_fallback' },
    { value: 'always', labelKey: 'settings_title_always' },
  ] },
  extraField('chat_max_parallel'),
  extraField('image_backfill_enabled', 'checkbox'), extraField('image_backfill_dry_run', 'checkbox'),
  extraField('image_backfill_forum_id'), { ...extraField('image_backfill_auto_interval_seconds', 'number', 0.01), step: 0.01 },
  extraField('image_backfill_daily_limit'), extraField('image_backfill_max_pages'),
  extraField('auto_signin_enabled', 'checkbox'), { ...extraField('cookie_refresh_interval_hours', 'number', 0.01), step: 0.01 },
]
function section(key: string, fields: string[]): SectionSpec {
  const original = SECTIONS.find(item => item.key === key)
  return {
    key, titleKey: original?.titleKey || 'settings_section_' + key,
    descKey: original?.descKey || 'settings_section_' + key + '_desc',
    fields: fields.map(name => ALL_FIELDS.find(field => field.key === name)!).filter(Boolean),
  }
}
const COMMON_SECTIONS = [
  section('archive', SECTIONS.find(item => item.key === 'archive')!.fields.map(field => field.key)),
  section('llm', ['llm_base_url', 'llm_api_key', 'llm_model']),
  section('embedded', ['chat_timeout', 'chat_max_parallel']),
  section('export', ['export_default_strategy', 'export_stale_after_hours', 'novel_txt_include_filtered_notes']),
  section('jobs', ['jobs_enabled', 'worker_parallelism', 'auto_signin_enabled', 'image_backfill_enabled', 'image_backfill_dry_run', 'image_backfill_daily_limit']),
]
const ADVANCED_SECTIONS = [
  section('worker', ['worker_poll_seconds', 'worker_lease_seconds', 'worker_heartbeat_seconds']),
  section('backfill', ['image_backfill_forum_id', 'image_backfill_auto_interval_seconds', 'image_backfill_max_pages']),
  section('rag', ['rag_enabled', 'rag_base_url', 'rag_api_key', 'rag_embedding_model', 'rag_embedding_dimensions', 'rag_hybrid_fts_candidates', 'rag_hybrid_vector_candidates']),
  section('maintenance', ['backup_keep_count', 'cleanup_staging_older_than_hours', 'cookie_refresh_interval_hours', 'novel_txt_debug_markers', 'rag_debug_indexing']),
  section('hermes', ['chat_backend', 'hermes_host', 'hermes_port', 'hermes_model', 'hermes_api_key', 'chat_streaming_enabled']),
  section('title', ['title_parse_mode', 'common_scanlation_groups', 'common_authors']),
]

function valueToInput(field: FieldSpec, payload: SettingsResponse | null) {
  if (!payload) return ''
  const raw = payload.values[field.key]
  if (field.kind === 'checkbox') return raw ? '1' : '0'
  if (field.kind === 'textarea' || field.kind === 'tags') return Array.isArray(raw) ? raw.join('\n') : String(raw ?? '')
  return raw == null ? '' : String(raw)
}

function fieldPlaceholder(field: FieldSpec, payload: SettingsResponse, value: string, t: (key: string) => string) {
  if (field.key === 'hermes_api_key' && payload.configured?.hermes_api_key && !value) {
    return t('settings_placeholder_hermes_api_key_configured')
  }
  return field.placeholderKey ? t(field.placeholderKey) : undefined
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

function TagEditor({ id, value, disabled, onChange }: { id: string; value: string; disabled: boolean; onChange: (v: string) => void }) {
  const { t } = useI18n()
  const [query, setQuery] = useState('')
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
      <input id={id} value={query} placeholder={t('settings_dictionary_search')} onChange={e => setQuery(e.target.value)} />
      <small>{tags.length} {t('settings_dictionary_count')}</small>
      <div className="settings-tag-list">
      {tags.filter(tag => tag.toLowerCase().includes(query.toLowerCase())).map(tag => (
        <span key={tag} className="tag-chip">
          {tag}
          {!disabled && <button type="button" className="tag-chip-x" onClick={() => remove(tag)} aria-label={`Remove ${tag}`}>&times;</button>}
        </span>
      ))}
      </div>
      {!disabled && (
        <input
          className="tag-editor-input"
          value={draft}
          aria-label={t('settings_dictionary_add')}
          placeholder={t('settings_dictionary_add')}
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
    const parsed = Number(raw)
    if (!Number.isFinite(parsed) || ((field.step || 1) >= 1 && !Number.isInteger(parsed)) || (field.min !== undefined && parsed < field.min) || (field.max !== undefined && parsed > field.max)) throw new Error(t('settings_invalid_number', { label: t(field.labelKey) }))
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
  modelTest,
  testingModel,
  testModel,
  feedback,
}: {
  section: SectionSpec
  payload: SettingsResponse
  formValues: Record<string, string>
  setFormValues: React.Dispatch<React.SetStateAction<Record<string, string>>>
  saving: boolean
  saveSection: (section: SectionSpec) => Promise<void>
  renderModelSelect: (field: FieldSpec, locked: boolean) => JSX.Element
  modelError: string | null
  modelTest: { ok: boolean; text: string } | null
  testingModel: boolean
  testModel: () => Promise<void>
  feedback?: { error: boolean; text: string }
}) {
  const { t } = useI18n()
  const dirtyCount = changedFieldsForSection(section, payload, formValues).length

  return (
    <section className="panel settings-section" id={`settings-section-${section.key}`}>
      <div className="settings-section-head">
        <div>
          <div className="settings-section-title-row">
            <h3 className="settings-section-title">{t(section.titleKey)}</h3>
            {dirtyCount > 0 && <Badge status="warn">{t('settings_dirty_count', { count: dirtyCount })}</Badge>}
            <Badge status={sectionSummaryKey(section, payload) === 'settings_effect_immediate' ? 'ok' : 'warn'}>
              {t(sectionSummaryKey(section, payload))}
            </Badge>
          </div>
          <p className="settings-copy">{t(section.descKey)}</p>
        </div>
        <button type="button" className="btn-primary settings-section-save" onClick={() => void saveSection(section)} disabled={saving || dirtyCount === 0}>
          {saving ? t('running') : t('save')}
        </button>
      </div>
      {feedback && <div role={feedback.error ? 'alert' : 'status'} className={`settings-note ${feedback.error ? 'settings-note-error' : 'settings-note-ok'}`}>{feedback.text}</div>}
      <div className="settings-grid">
        {section.fields.map(field => {
          const locked = payload.locked_fields.includes(field.key) || Boolean(payload.readonly_fields?.includes(field.key))
          const source = payload.sources[field.key] || 'default'
          const effect = payload.effects[field.key] || 'immediate'
          return (
            <div key={field.key} className={`settings-field settings-field-${field.kind}`} style={field.fullWidth ? { gridColumn: '1 / -1' } : undefined}>
              <div className="settings-field-head">
                <label htmlFor={`setting-${field.key}`} className="settings-field-label">{t(field.labelKey)}</label>
                <div className="settings-field-badges">
                  <Badge status={effectBadgeStatus(effect)}>{t(effectLabelKey(effect))}</Badge>
                  {locked && <Badge status="warn">{t('settings_locked')}</Badge>}
                </div>
              </div>
              {field.kind === 'tags' ? (
                <TagEditor
                  id={`setting-${field.key}`}
                  value={formValues[field.key] || ''}
                  disabled={locked}
                  onChange={v => setFormValues(prev => ({ ...prev, [field.key]: v }))}
                />
              ) : field.kind === 'textarea' ? (
                <textarea
                  id={`setting-${field.key}`}
                  rows={field.rows || 4}
                  value={formValues[field.key] || ''}
                  disabled={locked}
                  placeholder={fieldPlaceholder(field, payload, formValues[field.key] || '', t)}
                  onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
                />
              ) : field.kind === 'select' ? (
                field.key === 'llm_model'
                  ? renderModelSelect(field, locked)
                  : (
                    <select
                      id={`setting-${field.key}`}
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
                    id={`setting-${field.key}`}
                    type="checkbox"
                    checked={(formValues[field.key] || '0') === '1'}
                    disabled={locked}
                    onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.checked ? '1' : '0' }))}
                  />
                  <span>{locked ? t('settings_locked_edit') : t('settings_toggle_hint')}</span>
                </label>
              ) : (
                <input
                  id={`setting-${field.key}`}
                  type={field.kind === 'password' ? 'password' : field.kind === 'number' ? 'number' : 'text'}
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  value={formValues[field.key] || ''}
                  disabled={locked}
                  placeholder={fieldPlaceholder(field, payload, formValues[field.key] || '', t)}
                  onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))}
                />
              )}
              <small className="settings-help">
                {t(field.helpKey)}
                {field.inheritable && source === 'derived' && ` · ${t('settings_inherit_from_llm')}`}
              </small>
              <small className="settings-source">
                {t('settings_current_source')}: {sourceLabel(t, source)}
                {field.kind === 'password' && payload.configured?.[field.key] && ` · ${t('settings_secret_configured')}`}
              </small>
              {payload.pending_fields?.includes(field.key) && field.kind !== 'password' && <small className="settings-source">
                {t('settings_active_value')}: {String(payload.active_values?.[field.key] ?? '—')} · {t('settings_pending_fields')}
              </small>}
              {field.key === 'llm_model' && modelError && <small className="settings-source settings-source-error">{modelError}</small>}
            </div>
          )
        })}
      </div>
      {section.key === 'llm' && <div className="settings-model-test">
        <button type="button" className="btn-subtle" onClick={() => void testModel()} disabled={testingModel || saving}>
          {t(testingModel ? 'settings_model_testing' : 'settings_model_test')}
        </button>
        <span className="settings-copy">{t('settings_model_test_help')}</span>
        {modelTest && <div role={modelTest.ok ? 'status' : 'alert'} className={`settings-note ${modelTest.ok ? 'settings-note-ok' : 'settings-note-error'}`}>{modelTest.text}</div>}
      </div>}
    </section>
  )
}

function ScheduledTasksPanel({ onError }: { onError: (e: unknown) => void }) {
  const [tasks, setTasks] = useState<ScheduledArchiveTask[]>([])
  const [runs, setRuns] = useState<Record<string, ScheduledArchiveRun[]>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [name, setName] = useState('')
  const [tid, setTid] = useState('')
  const [forumId, setForumId] = useState('')
  const [kind, setKind] = useState<'at' | 'cron'>('at')
  const [at, setAt] = useState('')
  const [cron, setCron] = useState('0 8 * * *')
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [mode, setMode] = useState<'text_only' | 'full'>('text_only')
  const reload = async () => { const result = await api.scheduledTasks(); setTasks(result.items) }
  const perform = async (work: () => Promise<void>) => {
    setBusy(true); setMessage('')
    try { await work() }
    catch (e) { setMessage((e as Error).message); onError(e) }
    finally { setBusy(false) }
  }
  useEffect(() => { void perform(reload) }, [])
  const create = async () => {
    if (!name.trim() || !Number.isSafeInteger(Number(tid)) || Number(tid) < 1 || (forumId && (!Number.isSafeInteger(Number(forumId)) || Number(forumId) < 1))) throw new Error('请填写名称、有效 TID 和可选板块 ID。')
    if (kind === 'at' && (!/(Z|[+-]\d{2}:\d{2})$/i.test(at) || !Number.isFinite(Date.parse(at)))) throw new Error('单次时间需包含时区，例如 2026-10-01T08:00:00+08:00。')
    const body: ScheduledArchiveInput = { name: name.trim(), schedule_kind: kind, ...(kind === 'at' ? { at } : { cron }), timezone, action: 'archive_thread', arguments: { tid: Number(tid), mode, ...(forumId ? { forum_id: Number(forumId) } : {}) }, enabled: true }
    await api.createScheduledTask(body); await reload(); setMessage('定时归档已创建。'); setName(''); setTid('')
  }
  const toggle = async (task: ScheduledArchiveTask) => {
    await api.updateScheduledTask(task.task_id, { name: task.name, schedule_kind: task.schedule_kind, at: task.at, cron: task.cron, timezone: task.timezone, action: task.action, arguments: task.arguments, enabled: !task.enabled, expected_revision: task.revision })
    await reload()
  }
  const readRuns = async (id: string) => { const result = await api.scheduledTaskRuns(id); setRuns(previous => ({ ...previous, [id]: result.items })) }
  return <section className="panel settings-section">
    <h2 className="settings-section-title">定时归档</h2>
    <p className="settings-copy">按指定时间归档单个帖子。到期创建后台归档任务，完成情况以 Job 状态为准。</p>
    <form onSubmit={e => { e.preventDefault(); void perform(create) }} className="scheduled-task-form">
      <label>名称<input required value={name} onChange={e => setName(e.target.value)} /></label>
      <label>帖子 TID<input required inputMode="numeric" value={tid} onChange={e => setTid(e.target.value)} /></label>
      <label>板块 ID（可选）<input inputMode="numeric" value={forumId} onChange={e => setForumId(e.target.value)} /></label>
      <label>归档内容<select value={mode} onChange={e => setMode(e.target.value as typeof mode)}><option value="text_only">仅文本</option><option value="full">完整归档（含图片）</option></select></label>
      <label>执行方式<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="at">单次</option><option value="cron">周期</option></select></label>
      {kind === 'at' ? <label>执行时间（含时区）<input required placeholder="2026-10-01T08:00:00+08:00" value={at} onChange={e => setAt(e.target.value)} /></label> : <label>Cron（分 时 日 月 周）<input required value={cron} onChange={e => setCron(e.target.value)} /></label>}
      <label>时区<input required value={timezone} onChange={e => setTimezone(e.target.value)} /></label>
      <button disabled={busy} type="submit">创建定时归档</button>
    </form>
    <button disabled={busy} onClick={() => void perform(reload)}>刷新任务</button>
    {message && <p role="status">{message}</p>}
    {tasks.map(task => <article key={task.task_id} className="scheduled-task-item">
      <h3>{task.name} · {task.enabled ? '已启用' : '已暂停'}</h3>
      <p>归档 TID {task.arguments.tid} · {task.arguments.mode === 'full' ? '含图片' : '仅文本'}{task.arguments.forum_id ? ` · 板块 ${task.arguments.forum_id}` : ''}</p>
      <p>{task.schedule_kind === 'at' ? `单次：${task.at}` : `Cron：${task.cron}`} · {task.timezone}</p>
      <div className="settings-session-actions">
        <button disabled={busy} onClick={() => void perform(() => toggle(task))}>{task.enabled ? '暂停' : '启用'}</button>
        <button disabled={busy} onClick={() => void perform(async () => { await api.triggerScheduledTask(task.task_id, task.revision); await readRuns(task.task_id); await reload() })}>立即执行一次</button>
        <button disabled={busy} onClick={() => void perform(() => readRuns(task.task_id))}>查看运行记录</button>
        <button disabled={busy} onClick={() => void perform(async () => { await api.archiveScheduledTask(task.task_id, task.revision); await reload(); setMessage('已移除定时任务，历史运行记录仍保留。') })}>移除定时任务</button>
      </div>
      {runs[task.task_id]?.length === 0 && <p>暂无运行记录。</p>}
      {runs[task.task_id]?.map((run, index) => <p key={run.run_id || index}>{run.scheduled_for && `${new Date(run.scheduled_for).toLocaleString()} · `}{run.status || '状态待确认'}{run.job_id && <> · <Link to={`/jobs/${encodeURIComponent(run.job_id)}`}>Job {run.job_id}</Link></>}{run.error && ` · ${run.error}`}</p>)}
    </article>)}
  </section>
}

function SkillReviewPanel({ onError }: { onError: (e: unknown) => void }) {
  const [items, setItems] = useState<SkillReviewProposal[]>([])
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [message, setMessage] = useState('')
  const load = async () => {
    setBusy(true)
    try { const result = await api.skillReviews(); setItems(result.proposals); setLoaded(true); setMessage('') }
    catch (e) { setMessage((e as Error).message); onError(e) }
    finally { setBusy(false) }
  }
  useEffect(() => { void load() }, [])
  const review = async (id: string, approve: boolean) => {
    setBusy(true)
    try {
      const result = await api.reviewSkill(id, approve)
      setItems(previous => previous.map(item => item.id === id ? result : item))
      setMessage(approve ? '已批准，后续读取该 Skill 时使用新版本。' : '已拒绝，本次提案不会生效。')
    } catch (e) { setMessage('审核失败，请刷新后检查提案状态：' + (e as Error).message); onError(e) }
    finally { setBusy(false) }
  }
  return <section className="panel settings-section">
    <h2 className="settings-section-title">项目 Skill 修改审核</h2>
    <p className="settings-copy">助手提出的流程指导修改会列在这里。检查修改理由和差异后，批准或拒绝；批准不会扩大工具权限。</p>
    <button disabled={busy} onClick={() => void load()}>刷新提案</button>
    {busy && <p role="status">正在处理…</p>}
    {message && <p role="status">{message}</p>}
    {loaded && !items.some(item => item.status === 'pending') && <p className="settings-copy">暂无待审核提案。</p>}
    {[...items].sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending') || b.created_at - a.created_at).map(item => <details key={item.id} open={item.status === 'pending'}>
      <summary>{item.name} · {{ pending: '待审核', approved: '已批准', rejected: '已拒绝' }[item.status]} · {new Date(item.created_at * 1000).toLocaleString()}</summary>
      <p>{item.reason}</p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 400, overflowY: 'auto' }}>{item.diff}</pre>
      {item.status === 'pending' && <div className="settings-session-actions">
        <button disabled={busy} onClick={() => void review(item.id, true)}>批准修改</button>
        <button disabled={busy} onClick={() => void review(item.id, false)}>拒绝修改</button>
      </div>}
    </details>)}
  </section>
}

function AgentGuidanceEditor({ onDirtyChange, onError }: { onDirtyChange: (dirty: boolean) => void; onError: (e: unknown) => void }) {
  const [saved, setSaved] = useState<{ content: string; revision: number } | null>(null)
  const [content, setContent] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const dirty = saved !== null && content !== saved.content
  useEffect(() => { onDirtyChange(dirty) }, [dirty, onDirtyChange])
  const load = async () => {
    setBusy(true)
    try { const next = await api.agentGuidance(); setSaved(next); setContent(next.content); setMessage('') }
    catch (e) { setMessage((e as Error).message); onError(e) }
    finally { setBusy(false) }
  }
  useEffect(() => { void load() }, [])
  const save = async () => {
    if (!saved) return
    setBusy(true)
    try { const next = await api.saveAgentGuidance(content, saved.revision); setSaved(next); setMessage('已保存，下一轮对话生效。') }
    catch (e) { onError(e); setMessage('保存失败，草稿已保留。若其他对话已修改内容，请复制草稿后重新加载。' + (e as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="panel settings-section">
    <h2 className="settings-section-title">Agent 长期指导 · AGENTS.md</h2>
    <p className="settings-copy">编辑助手的长期偏好与工作指导。聊天中经你确认的记忆也保存在这里；保存后从下一轮对话生效。</p>
    <textarea aria-label="Agent 长期指导" rows={12} style={{ width: '100%', boxSizing: 'border-box' }} value={content} disabled={busy || !saved} onChange={e => setContent(e.target.value)} />
    <div className="settings-session-actions">
      <button disabled={busy || !dirty} onClick={() => void save()}>保存指导</button>
      <button disabled={busy} onClick={() => { if (!dirty || window.confirm('重新加载将丢弃未保存的指导，是否继续？')) void load() }}>重新加载</button>
    </div>
    {message && <p role="status">{message}</p>}
  </section>
}

export function Settings({
  advanced = false,
  sectionOnly,
  onDirtyChange,
  onSaved,
}: {
  advanced?: boolean
  sectionOnly?: 'llm'
  onDirtyChange?: (dirty: boolean) => void
  onSaved?: () => void
}) {
  const { t } = useI18n()
  const [payload, setPayload] = useState<SettingsResponse | null>(null)
  const [formValues, setFormValues] = useState<Record<string, string>>({})
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [modelLoading, setModelLoading] = useState(false)
  const [modelError, setModelError] = useState<string | null>(null)
  const [testingModel, setTestingModel] = useState(false)
  const [modelTest, setModelTest] = useState<{ ok: boolean; text: string } | null>(null)
  const modelLoadLock = useRef(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [authRequired, setAuthRequired] = useState(false)
  const [sessionProtected, setSessionProtected] = useState(true)
  const [sessionAvailable, setSessionAvailable] = useState(true)
  const [accessToken, setAccessToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [unlocking, setUnlocking] = useState(false)
  const [feedback, setFeedback] = useState<Record<string, { error: boolean; text: string }>>({})
  const [guidanceDirty, setGuidanceDirty] = useState(false)
  const [layoutDirty, setLayoutDirty] = useState(false)
  const [layoutReset, setLayoutReset] = useState(0)
  const dirty = guidanceDirty || layoutDirty || (payload ? ALL_FIELDS.some(field => changedFieldsForSection({ key: '', titleKey: '', descKey: '', fields: [field] }, payload, formValues).length > 0) : false)

  useEffect(() => { setModelTest(null) }, [formValues.llm_base_url, formValues.llm_api_key, formValues.llm_model])
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])

  const handleError = (e: unknown) => {
    const issue = e as Error & { code?: string }
    if (issue.code === 'SETTINGS_AUTH_REQUIRED') {
      setAuthRequired(true)
      setError(t('settings_session_expired'))
    } else setError(issue.message || t('error'))
  }
  const refresh = async (preserveDraft = false) => {
    const next = await api.settings()
    if (!preserveDraft) {
      setPayload(next)
      setFormValues(Object.fromEntries(ALL_FIELDS.map(field => [field.key, valueToInput(field, next)])))
      setLayoutDirty(false)
      setLayoutReset(value => value + 1)
    }
    const session = await api.settingsSession()
    setSessionProtected(session.required)
    setSessionAvailable(session.available !== false)
  }
  useEffect(() => {
    let active = true
    Promise.all([api.settings(), api.settingsSession()]).then(([next, session]) => {
      if (!active) return
      setPayload(next)
      setSessionProtected(session.required)
      setFormValues(Object.fromEntries(ALL_FIELDS.map(field => [field.key, valueToInput(field, next)])))
    }).catch(async e => {
      if (!active) return
      handleError(e)
      try {
        const session = await api.settingsSession()
        if (active) setSessionAvailable(session.available !== false)
      } catch { /* The original request error remains visible. */ }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    const currentUrl = location.href
    const resetDraft = () => {
      setFormValues(Object.fromEntries(ALL_FIELDS.map(field => [field.key, valueToInput(field, payload)])))
      setLayoutDirty(false)
      setLayoutReset(value => value + 1)
    }
    const guardHistory = () => {
      if (!window.confirm(t('settings_discard_prompt'))) history.pushState(null, '', currentUrl)
      else resetDraft()
    }
    const guard = (event: MouseEvent) => {
      const target = event.target as Element
      const link = target.closest('a[href]') as HTMLAnchorElement | null
      if (link && link.origin === location.origin && link.pathname !== location.pathname) {
        if (!window.confirm(t('settings_discard_prompt'))) {
          event.preventDefault(); event.stopPropagation()
        } else resetDraft()
      }
    }
    window.addEventListener('beforeunload', warn)
    window.addEventListener('popstate', guardHistory)
    document.addEventListener('click', guard, true)
    return () => { window.removeEventListener('beforeunload', warn); window.removeEventListener('popstate', guardHistory); document.removeEventListener('click', guard, true) }
  }, [dirty, t, payload])

  const loadModels = async () => {
    if (modelLoadLock.current) return
    modelLoadLock.current = true; setModelLoading(true); setModelError(null)
    try { setModelOptions((await api.settingsModels({ base_url: formValues.llm_base_url || '', api_key: formValues.llm_api_key || '' })).models) }
    catch (e) { setModelError((e as Error).message); handleError(e) }
    finally { modelLoadLock.current = false; setModelLoading(false) }
  }
  const testModel = async () => {
    setTestingModel(true); setModelTest(null)
    try {
      const result = await api.testModelConnection({
        base_url: (formValues.llm_base_url || '').trim(),
        api_key: formValues.llm_api_key || '',
        model: (formValues.llm_model || '').trim(),
      })
      setModelTest({ ok: result.ok, text: `${result.message} · ${result.model} · ${result.elapsed_ms} ms${result.http_status ? ` · HTTP ${result.http_status}` : ''}` })
    } catch (e) {
      const issue = e as Error
      setModelTest({ ok: false, text: issue.message || t('error') })
      handleError(e)
    } finally { setTestingModel(false) }
  }
  const renderModelSelect = (field: FieldSpec, locked: boolean) => (
    <div className="settings-select-wrap">
      <input id={`setting-${field.key}`} list="settings-model-options" value={formValues[field.key] || ''} disabled={locked}
        onChange={e => setFormValues(v => ({ ...v, [field.key]: e.target.value }))} />
      <datalist id="settings-model-options">{modelOptions.map(model => <option key={model} value={model} />)}</datalist>
      <button type="button" className="btn-subtle settings-model-refresh" onClick={() => void loadModels()} disabled={locked || modelLoading}>
        {modelLoading ? t('loading') : t('settings_models_refresh')}
      </button>
    </div>
  )
  const saveSection = async (current: SectionSpec) => {
    if (!payload) return
    const changedFields = changedFieldsForSection(current, payload, formValues)
    if (!changedFields.length) return
    setSaving(true); setError(null)
    try {
      const values: Record<string, unknown> = {}
      for (const field of changedFields) values[field.key] = parseFieldValue(field, formValues[field.key] ?? '', t)
      const next = await api.updateSettings(values, payload.revision)
      setPayload(next)
      setFormValues(previous => {
        const updated = { ...previous }
        current.fields.forEach(field => { updated[field.key] = valueToInput(field, next) })
        return updated
      })
      setFeedback(previous => ({ ...previous, [current.key]: { error: false, text: savedMessage(next, t) } }))
      onSaved?.()
    } catch (e) {
      setFeedback(previous => ({ ...previous, [current.key]: { error: true, text: (e as Error).message } }))
      handleError(e)
    } finally { setSaving(false) }
  }
  const unlock = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!accessToken.trim() || unlocking) return
    setUnlocking(true); setError(null)
    try {
      await api.loginSettings(accessToken.trim())
      await refresh(Boolean(payload))
      setAuthRequired(false); setAccessToken('')
    } catch (e) { handleError(e) }
    finally { setUnlocking(false) }
  }
  const logout = async () => {
    if (dirty && !window.confirm(t('settings_discard_prompt'))) return
    try { await api.logoutSettings(); setPayload(null); setFormValues({}); setAuthRequired(true); setError(null) }
    catch (e) { handleError(e) }
  }
  if (loading) return <LoadingIndicator label={t('loading')} />
  const authView = (
    <div className={`settings-auth-shell ${sectionOnly ? 'settings-auth-shell-embedded' : ''}`}>
      <section className="panel settings-auth">
        <div className="settings-eyebrow">{t('settings_admin_area')}</div>
        <h1>{t('settings_auth_title')}</h1>
        <p className="settings-auth-copy">{payload ? t('settings_session_expired') : t('settings_auth_desc')}</p>
        <form onSubmit={unlock}>
          <label htmlFor="settings-access-token">{t('settings_access_token')}</label>
          <div className="settings-token-input">
            <input id="settings-access-token" type={showToken ? 'text' : 'password'} autoComplete="off" autoFocus
              value={accessToken} onChange={e => setAccessToken(e.target.value)} disabled={unlocking || !sessionAvailable} />
            <button type="button" className="btn-subtle" onClick={() => setShowToken(value => !value)} aria-pressed={showToken}>
              {t(showToken ? 'settings_hide' : 'settings_show')}
            </button>
          </div>
          {error && <p role="alert" className="settings-note settings-note-error">{error}</p>}
          {!sessionAvailable && <p role="alert" className="settings-note settings-note-error">{t('settings_session_unavailable')}</p>}
          <button className="btn-primary" type="submit" disabled={unlocking || !sessionAvailable || !accessToken.trim()}>{t(unlocking ? 'settings_unlocking' : 'settings_enter')}</button>
        </form>
        <p className="settings-auth-footnote">{t('settings_session_hint')}</p>
        {!sectionOnly && <Link to="/chat">{t('settings_back_chat')}</Link>}
      </section>
    </div>
  )
  if (authRequired && !payload) return authView
  if (!payload) return <section className="panel"><h2>{t('settings_load_failed')}</h2><p role="alert">{error}</p>
    <button className="btn-subtle" onClick={() => void refresh().catch(handleError)}>{t('refresh')}</button></section>

  const sections = (sectionOnly ? [section(sectionOnly, ['llm_base_url', 'llm_api_key', 'llm_model'])] : advanced ? ADVANCED_SECTIONS : COMMON_SECTIONS)
    .filter(item => item.key !== 'embedded' || formValues.chat_backend === 'embedded')
    .map(item => ({
      ...item,
      fields: item.fields.filter(field => field.key in payload.values)
        .filter(field => item.key !== 'hermes' || field.key === 'chat_backend' || formValues.chat_backend === 'hermes')
        .filter(field => item.key !== 'rag' || field.key === 'rag_enabled' || formValues.rag_enabled === '1'),
    }))
  return (
    <>
    {authRequired && authView}
    <div className={`settings-page ${sectionOnly ? 'settings-page-embedded' : ''}`} style={authRequired ? { display: 'none' } : undefined}>
      {!sectionOnly && <>
      <header className="settings-page-header">
        <div><div className="settings-eyebrow">{t('settings_workspace')}</div>
          <h1>{t(advanced ? 'settings_advanced_page' : 'settings_title')}</h1>
          <p className="settings-copy">{t(advanced ? 'settings_advanced_page_desc' : 'settings_common_desc')}</p>
        </div>
        <div className="settings-session-actions">
          <Badge status={sessionProtected ? 'ok' : 'warn'}>{t(sessionProtected ? 'settings_session_verified' : 'settings_session_unprotected')}</Badge>
          {sessionProtected && <button type="button" className="btn-subtle" onClick={() => void logout()}>{t('settings_logout')}</button>}
        </div>
      </header>
      <nav className="settings-page-nav" aria-label={t('settings_title')}>
        <Link className={!advanced ? 'active' : ''} to="/settings" aria-current={!advanced ? 'page' : undefined}>{t('settings_common_page')}</Link>
        <Link className={advanced ? 'active' : ''} to="/settings/advanced" aria-current={advanced ? 'page' : undefined}>{t('settings_advanced_page')}</Link>
        <button className="btn-subtle" disabled={saving} onClick={() => {
          if (dirty && !window.confirm(t('settings_discard_prompt'))) return
          void refresh().then(() => { setFeedback({}); setError(null) }).catch(handleError)
        }}>{t('refresh')}</button>
      </nav>
      </>}
      {error && <div role="alert" className="settings-note settings-note-error">{error}</div>}
      {!sectionOnly && (payload.pending_fields?.length || 0) > 0 && <div className="settings-note settings-note-ok" role="status">{t('settings_pending', { count: payload.pending_fields!.length })}<div className="settings-pending-links">{sections.filter(section => section.fields.some(field => payload.pending_fields?.includes(field.key))).map(section => <a key={section.key} href={`#settings-section-${section.key}`}>{t(section.titleKey)} ↓</a>)}</div></div>}
      <div className="settings-form">
        {!sectionOnly && !advanced && <AgentGuidanceEditor key={layoutReset} onDirtyChange={setGuidanceDirty} onError={handleError} />}
        {!sectionOnly && !advanced && payload.values.db_backend === 'postgres' && <ScheduledTasksPanel key={`tasks-${layoutReset}`} onError={handleError} />}
        {!sectionOnly && !advanced && payload.values.db_backend !== 'postgres' && <section className="panel settings-section"><h2 className="settings-section-title">定时归档</h2><p className="settings-copy">定时归档需要 PostgreSQL；当前数据库不支持此功能。</p></section>}
        {!sectionOnly && !advanced && <SkillReviewPanel key={`skills-${layoutReset}`} onError={handleError} />}
        {sections.map(current => <div key={current.key}>
          <SettingsSection section={current} payload={payload} formValues={formValues} setFormValues={setFormValues}
            saving={saving} saveSection={saveSection} renderModelSelect={renderModelSelect} modelError={modelError}
            modelTest={modelTest} testingModel={testingModel} testModel={testModel} feedback={feedback[current.key]} />
          {current.key === 'rag' && formValues.rag_enabled !== '1' && <p className="settings-inline-summary">{t('settings_rag_disabled_summary')} · {String(payload.values.rag_embedding_model || '—')}</p>}
        </div>)}
        {advanced && <>
          <section className="panel settings-section">
            <h2 className="settings-section-title">{t('settings_security_title')}</h2>
            <p className="settings-copy">{t('settings_session_hint')}</p>
            <p className="settings-copy">{t('settings_token_managed')}</p>
          </section>
          <section className="panel settings-section">
            <h2 className="settings-section-title">{t('settings_deployment_title')}</h2>
            <p className="settings-copy">{t('settings_deployment_desc')}</p>
            <dl className="settings-deployment">
              <dt>{t('settings_config_file')}</dt><dd>{payload.config_path}</dd>
              <dt>Chunker</dt><dd>{payload.chunker_version || '—'}</dd>
              {['db_backend', 'db_schema', 'db_pool_min', 'db_pool_max', 'db_sslmode'].filter(key => key in payload.values).map(key => <div key={key}><dt>{key}</dt><dd>{String(payload.values[key] ?? '—')}</dd></div>)}
              <dt>{t('settings_pending_fields')}</dt><dd>{payload.pending_fields?.join(', ') || t('settings_none')}</dd>
            </dl>
            <details><summary>{t('settings_locked_summary', { count: payload.locked_fields.length })}</summary>
              <ul className="settings-lock-list">{payload.locked_fields.map(key => <li key={key}><code>{key}</code><span>{t('settings_source_env')}</span></li>)}</ul>
            </details>
          </section>
          <TableLayoutEditor key={layoutReset} value={payload.values.table_layouts} revision={payload.revision}
            onDirtyChange={setLayoutDirty} onAuthRequired={handleError} onSaved={setPayload} />
        </>}
        {!sectionOnly && !advanced && <Link className="panel settings-advanced-link" to="/settings/advanced"><div><strong>{t('settings_advanced_page')}</strong><p className="settings-copy">{t('settings_advanced_page_desc')}</p></div><span aria-hidden="true">→</span></Link>}
      </div>
    </div>
    </>
  )
}
