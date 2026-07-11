import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import { api, type DaemonStatus } from '../api/client'

interface TaskStatusContextValue {
  taskStatus: DaemonStatus | null
  refreshTaskStatus: () => void
  controlJobs: (action: 'pause' | 'resume') => Promise<void>
  controlBusy: boolean
}

const TaskStatusContext = createContext<TaskStatusContextValue>({
  taskStatus: null,
  refreshTaskStatus: () => {},
  controlJobs: async () => {},
  controlBusy: false,
})

export function TaskStatusProvider({ children }: { children: ReactNode }) {
  const [taskStatus, setTaskStatus] = useState<DaemonStatus | null>(null)
  const [controlBusy, setControlBusy] = useState(false)
  const [tick, setTick] = useState(0)

  const refreshTaskStatus = useCallback(() => setTick(t => t + 1), [])

  useEffect(() => {
    let active = true
    // Immediate fetch on mount and on every tick (e.g. after controlJobs).
    api.daemonStatus()
      .then((status) => { if (active) setTaskStatus(status) })
      .catch(() => {})
    const timer = window.setInterval(() => {
      api.daemonStatus()
        .then((status) => { if (active) setTaskStatus(status) })
        .catch(() => {})
    }, 5000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [tick])

  const controlJobs = useCallback(async (action: 'pause' | 'resume') => {
    setControlBusy(true)
    try {
      await api.controlJobs(action)
      refreshTaskStatus()
    } finally {
      setControlBusy(false)
    }
  }, [refreshTaskStatus])

  return (
    <TaskStatusContext.Provider value={{ taskStatus, refreshTaskStatus, controlJobs, controlBusy }}>
      {children}
    </TaskStatusContext.Provider>
  )
}

export function useTaskStatus() {
  return useContext(TaskStatusContext)
}
