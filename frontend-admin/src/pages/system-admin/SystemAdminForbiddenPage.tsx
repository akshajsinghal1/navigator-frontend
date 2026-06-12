import { useNavigate } from 'react-router-dom'

export function SystemAdminForbiddenPage() {
  const navigate = useNavigate()
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F1F5F9' }}>
      <div style={{ textAlign: 'center', background: '#fff', borderRadius: 14, padding: 48, maxWidth: 420, boxShadow: '0 4px 24px rgba(0,0,0,0.08)' }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>🚫</div>
        <h2 style={{ margin: '0 0 8px', color: '#1E293B' }}>Access Denied</h2>
        <p style={{ color: '#6B7280', fontSize: 14, marginBottom: 24 }}>
          You do not have access to the system admin console.<br />
          Contact your administrator to request access.
        </p>
        <button
          onClick={() => navigate('/system-admin/login')}
          style={{ background: '#1E293B', color: '#fff', border: 'none', padding: '9px 20px', borderRadius: 7, cursor: 'pointer', fontSize: 14 }}
        >
          Back to Login
        </button>
      </div>
    </div>
  )
}
