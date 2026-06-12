import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../../lib/supabase'
import { api } from '../../lib/api'
import type { MeResponse } from '../../types/systemAdmin'

export function SystemAdminLoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    const { error: authError } = await supabase.auth.signInWithPassword({ email, password })
    if (authError) {
      setError('Invalid email or password.')
      setLoading(false)
      return
    }

    try {
      await api.get<MeResponse>('/api/system-admin/me')
      navigate('/system-admin/organizations')
    } catch (e: any) {
      const code = e?.detail?.code
      if (code === 'SYSTEM_ADMIN_FORBIDDEN' || code === 'SYSTEM_ADMIN_INACTIVE') {
        await supabase.auth.signOut()
        navigate('/system-admin/forbidden')
      } else {
        setError('Login failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={pageStyle}>
      <div style={card}>
        <div style={{ marginBottom: 28, textAlign: 'center' }}>
          <h1 style={{ margin: '0 0 6px', fontSize: 22, fontWeight: 700, color: '#1E293B' }}>
            System Admin
          </h1>
          <p style={{ margin: 0, color: '#6B7280', fontSize: 14 }}>Internal team access only</p>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={fieldGroup}>
            <label style={labelStyle}>Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              style={inputStyle}
              placeholder="you@company.com"
            />
          </div>
          <div style={fieldGroup}>
            <label style={labelStyle}>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              style={inputStyle}
              placeholder="••••••••"
            />
          </div>

          {error && (
            <div style={{ background: '#FEE2E2', color: '#991B1B', borderRadius: 7, padding: '10px 14px', fontSize: 13, marginBottom: 16 }}>
              {error}
            </div>
          )}

          <button type="submit" disabled={loading} style={submitBtn}>
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}

const pageStyle: React.CSSProperties = {
  minHeight: '100vh', display: 'flex', alignItems: 'center',
  justifyContent: 'center', background: '#F1F5F9',
}
const card: React.CSSProperties = {
  background: '#fff', borderRadius: 14, padding: 36,
  width: 380, boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
}
const fieldGroup: React.CSSProperties = { marginBottom: 16 }
const labelStyle: React.CSSProperties = {
  display: 'block', fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 6,
}
const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '9px 12px',
  border: '1px solid #D1D5DB', borderRadius: 7, fontSize: 14,
}
const submitBtn: React.CSSProperties = {
  width: '100%', background: '#1E293B', color: '#fff', border: 'none',
  padding: '10px', borderRadius: 7, fontWeight: 600, fontSize: 15,
  cursor: 'pointer', marginTop: 4,
}
