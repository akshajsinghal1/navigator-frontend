import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { supabase } from './lib/supabase'
import { api } from './lib/api'
import type { MeResponse } from './types/systemAdmin'
import { SystemAdminLoginPage } from './pages/system-admin/SystemAdminLoginPage'
import { SystemAdminForbiddenPage } from './pages/system-admin/SystemAdminForbiddenPage'
import { SystemAdminOrganizationsPage } from './pages/system-admin/SystemAdminOrganizationsPage'
import { SystemAdminOrganizationDetailPage } from './pages/system-admin/SystemAdminOrganizationDetailPage'
import './index.css'

function ProtectedApp() {
  const navigate = useNavigate()
  const [me, setMe] = useState<MeResponse | null>(null)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    async function check() {
      const { data } = await supabase.auth.getSession()
      if (!data.session) {
        navigate('/system-admin/login')
        return
      }
      try {
        const meData = await api.get<MeResponse>('/api/system-admin/me')
        setMe(meData)
      } catch {
        navigate('/system-admin/forbidden')
      } finally {
        setChecking(false)
      }
    }
    check()
  }, [navigate])

  if (checking) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: '#9CA3AF' }}>Loading…</p>
      </div>
    )
  }

  if (!me) return null

  return (
    <Routes>
      <Route path="organizations" element={<SystemAdminOrganizationsPage me={me} />} />
      <Route path="organizations/:tab" element={<SystemAdminOrganizationsPage me={me} />} />
      <Route path="organizations/:organizationId/detail" element={<SystemAdminOrganizationDetailPage me={me} />} />
      <Route path="*" element={<Navigate to="/system-admin/organizations/pending" replace />} />
    </Routes>
  )
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/system-admin/login" replace />} />
        <Route path="/system-admin/login" element={<SystemAdminLoginPage />} />
        <Route path="/system-admin/forbidden" element={<SystemAdminForbiddenPage />} />
        <Route path="/system-admin/*" element={<ProtectedApp />} />
      </Routes>
    </BrowserRouter>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
