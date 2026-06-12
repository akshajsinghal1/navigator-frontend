import { NavLink, useNavigate } from 'react-router-dom'
import { supabase } from '../../lib/supabase'
import type { MeResponse } from '../../types/systemAdmin'
import { InternalStatusBadge } from './InternalStatusBadge'

const tabs = [
  { label: 'Pending', to: '/system-admin/organizations/pending' },
  { label: 'Approved', to: '/system-admin/organizations/approved' },
  { label: 'Rejected', to: '/system-admin/organizations/rejected' },
  { label: 'All', to: '/system-admin/organizations' },
]

export function SystemAdminLayout({
  me,
  children,
}: {
  me: MeResponse
  children: React.ReactNode
}) {
  const navigate = useNavigate()

  async function logout() {
    await supabase.auth.signOut()
    navigate('/system-admin/login')
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: '#F9FAFB' }}>
      {/* Header */}
      <header
        style={{
          background: '#1E293B',
          color: '#fff',
          padding: '0 24px',
          height: 56,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          flexShrink: 0,
        }}
      >
        <span style={{ fontWeight: 700, fontSize: 16, letterSpacing: 0.5 }}>System Admin</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 13, color: '#CBD5E1' }}>{me.user.email}</span>
          <InternalStatusBadge status={me.system_admin.role} />
          <button
            onClick={logout}
            style={{
              background: 'transparent',
              border: '1px solid #475569',
              color: '#CBD5E1',
              padding: '4px 12px',
              borderRadius: 6,
              cursor: 'pointer',
              fontSize: 13,
            }}
          >
            Logout
          </button>
        </div>
      </header>

      {/* Sidebar + Content */}
      <div style={{ display: 'flex', flex: 1 }}>
        <nav
          style={{
            width: 200,
            background: '#fff',
            borderRight: '1px solid #E5E7EB',
            padding: '24px 0',
            flexShrink: 0,
          }}
        >
          <p style={{ fontSize: 11, fontWeight: 600, color: '#9CA3AF', padding: '0 20px 8px', letterSpacing: 1 }}>
            ORGANIZATIONS
          </p>
          {tabs.map((t) => (
            <NavLink
              key={t.to}
              to={t.to}
              end={t.to === '/system-admin/organizations'}
              style={({ isActive }) => ({
                display: 'block',
                padding: '9px 20px',
                fontSize: 14,
                color: isActive ? '#1E293B' : '#6B7280',
                fontWeight: isActive ? 600 : 400,
                background: isActive ? '#F1F5F9' : 'transparent',
                textDecoration: 'none',
                borderLeft: isActive ? '3px solid #3B82F6' : '3px solid transparent',
              })}
            >
              {t.label}
            </NavLink>
          ))}
        </nav>

        <main style={{ flex: 1, padding: 32, overflow: 'auto' }}>{children}</main>
      </div>
    </div>
  )
}
