import { useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { useI18n } from '../context/I18nContext'
import { useTaskStatus } from '../context/TaskStatusContext'

export function GlobalJobControl() {
  const { t, tx } = useI18n()
  const { taskStatus, controlJobs, controlBusy } = useTaskStatus()
  const [actionFailed, setActionFailed] = useState(false)
  const enabled = taskStatus?.jobs_enabled ?? true

  const handleToggle = async () => {
    if (!taskStatus) return
    setActionFailed(false)
    try {
      await controlJobs(enabled ? 'pause' : 'resume')
    } catch {
      setActionFailed(true)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {actionFailed && <span className="text-xs text-destructive" role="alert">{tx('更新失败', 'Update failed')}</span>}
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={tx('任务执行总开关', 'Global task execution switch')}
        onClick={() => void handleToggle()}
        disabled={!taskStatus || controlBusy}
        className="inline-flex shrink-0 items-center gap-2 rounded border border-border bg-card px-3 py-2 text-sm font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
      >
        {enabled ? <Pause className="h-4 w-4" aria-hidden="true" /> : <Play className="h-4 w-4" aria-hidden="true" />}
        {controlBusy ? tx('更新中…', 'Updating…') : enabled ? t('pause_active_jobs') : t('resume_paused_jobs')}
      </button>
    </div>
  )
}
