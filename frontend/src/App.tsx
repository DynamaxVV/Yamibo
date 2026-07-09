import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ThemeProvider } from './context/ThemeContext'
import { I18nProvider } from './context/I18nContext'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { Jobs } from './pages/Jobs'
import { JobDetail } from './pages/JobDetail'
import { Threads } from './pages/Threads'
import { ThreadDetail } from './pages/ThreadDetail'
import { SeriesDetail, Exports } from './pages/Exports'
import { Series } from './pages/Series'
import { Review } from './pages/Review'
import { Forums } from './pages/Forums'
import { RemoteForum } from './pages/RemoteForum'
import { RemoteThreadDetail } from './pages/RemoteThreadDetail'
import { Logs } from './pages/Logs'
import { Rag } from './pages/Rag'
import { Settings } from './pages/Settings'
import { Chat } from './pages/Chat'

export default function App() {
  return (
    <I18nProvider>
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/jobs/:id" element={<JobDetail />} />
            <Route path="/threads" element={<Threads />} />
            <Route path="/threads/:tid" element={<ThreadDetail />} />
            <Route path="/series" element={<Series />} />
            <Route path="/series/:id" element={<SeriesDetail />} />
            <Route path="/review" element={<Review />} />
            <Route path="/exports" element={<Exports />} />
            <Route path="/forums" element={<Forums />} />
            <Route path="/forum" element={<RemoteForum />} />
            <Route path="/forum/:tid" element={<RemoteThreadDetail />} />
            <Route path="/rag" element={<Rag />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/logs" element={<Logs />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
    </I18nProvider>
  )
}
