import { lazy, Suspense, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './context/ThemeContext'
import { I18nProvider, useI18n } from './context/I18nContext'
import { TaskStatusProvider } from './context/TaskStatusContext'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      refetchOnWindowFocus: false,
    },
  },
})
import { Layout } from './components/Layout'

const Dashboard = lazy(() => import('./pages/Dashboard').then(({ Dashboard }) => ({ default: Dashboard })))
const Jobs = lazy(() => import('./pages/Jobs').then(({ Jobs }) => ({ default: Jobs })))
const JobDetail = lazy(() => import('./pages/JobDetail').then(({ JobDetail }) => ({ default: JobDetail })))
const Threads = lazy(() => import('./pages/Threads').then(({ Threads }) => ({ default: Threads })))
const ThreadDetail = lazy(() => import('./pages/ThreadDetail').then(({ ThreadDetail }) => ({ default: ThreadDetail })))
const Exports = lazy(() => import('./pages/Exports').then(({ Exports }) => ({ default: Exports })))
const SeriesDetail = lazy(() => import('./pages/Exports').then(({ SeriesDetail }) => ({ default: SeriesDetail })))
const Series = lazy(() => import('./pages/Series').then(({ Series }) => ({ default: Series })))
const Review = lazy(() => import('./pages/Review').then(({ Review }) => ({ default: Review })))
const Forums = lazy(() => import('./pages/Forums').then(({ Forums }) => ({ default: Forums })))
const RemoteForum = lazy(() => import('./pages/RemoteForum').then(({ RemoteForum }) => ({ default: RemoteForum })))
const RemoteThreadDetail = lazy(() => import('./pages/RemoteThreadDetail').then(({ RemoteThreadDetail }) => ({ default: RemoteThreadDetail })))
const Logs = lazy(() => import('./pages/Logs').then(({ Logs }) => ({ default: Logs })))
const Rag = lazy(() => import('./pages/Rag').then(({ Rag }) => ({ default: Rag })))
const Settings = lazy(() => import('./pages/Settings').then(({ Settings }) => ({ default: Settings })))
const Chat = lazy(() => import('./pages/Chat').then(({ Chat }) => ({ default: Chat })))

function PageLoading() {
  const { t } = useI18n()
  return (
    <div className="route-loading" role="status" aria-busy="true">
      <span className="route-loading__spinner" aria-hidden="true" />
      <span className="sr-only">{t('loading')}</span>
    </div>
  )
}

function loadPage(page: ReactNode) {
  return <Suspense fallback={<PageLoading />}>{page}</Suspense>
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <ThemeProvider>
          <TaskStatusProvider>
            <BrowserRouter>
              <Routes>
                <Route element={<Layout />}>
                  <Route path="/" element={loadPage(<Dashboard />)} />
                  <Route path="/jobs" element={loadPage(<Jobs />)} />
                  <Route path="/jobs/:id" element={loadPage(<JobDetail />)} />
                  <Route path="/threads" element={loadPage(<Threads />)} />
                  <Route path="/threads/:tid" element={loadPage(<ThreadDetail />)} />
                  <Route path="/series" element={loadPage(<Series />)} />
                  <Route path="/series/:id" element={loadPage(<SeriesDetail />)} />
                  <Route path="/review" element={loadPage(<Review />)} />
                  <Route path="/exports" element={loadPage(<Exports />)} />
                  <Route path="/forums" element={loadPage(<Forums />)} />
                  <Route path="/forum" element={loadPage(<RemoteForum />)} />
                  <Route path="/forum/:tid" element={loadPage(<RemoteThreadDetail />)} />
                  <Route path="/rag" element={loadPage(<Rag />)} />
                  <Route path="/chat" element={loadPage(<Chat />)} />
                  <Route path="/settings" element={loadPage(<Settings />)} />
                  <Route path="/settings/advanced" element={loadPage(<Settings advanced />)} />
                  <Route path="/logs" element={loadPage(<Logs />)} />
                </Route>
              </Routes>
            </BrowserRouter>
          </TaskStatusProvider>
        </ThemeProvider>
      </I18nProvider>
    </QueryClientProvider>
  )
}
