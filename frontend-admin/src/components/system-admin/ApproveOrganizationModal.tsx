import { useState } from 'react'
import { api } from '../../lib/api'
import type { ApproveResponse, OrgListItem } from '../../types/systemAdmin'

export function ApproveOrganizationModal({
  org,
  onClose,
  onSuccess,
}: {
  org: OrgListItem
  onClose: () => void
  onSuccess: (res: ApproveResponse) => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleApprove() {
    setLoading(true)
    setError(null)
    try {
      const res = await api.patch<ApproveResponse>(
        `/api/system-admin/organizations/${org.organization_id}/approve`,
        {}
      )
      onSuccess(res)
    } catch (e: any) {
      setError(e?.detail?.message || 'Failed to approve organization.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={overlay}>
      <div style={modal}>
        <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>Approve Organization?</h2>
        <p style={{ color: '#6B7280', margin: '0 0 20px', fontSize: 14 }}>
          This will activate the organization and allow the organization admin to access the Admin Panel.
        </p>
        <div style={{ background: '#F9FAFB', borderRadius: 8, padding: '12px 16px', marginBottom: 20 }}>
          <p style={{ margin: '0 0 4px', fontSize: 13 }}>
            <strong>Organization:</strong> {org.organization_name}
          </p>
          <p style={{ margin: 0, fontSize: 13 }}>
            <strong>Admin:</strong> {org.created_by.email}
          </p>
        </div>
        {error && <p style={{ color: '#DC2626', fontSize: 13, marginBottom: 12 }}>{error}</p>}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onClose} disabled={loading} style={btnSecondary}>Cancel</button>
          <button onClick={handleApprove} disabled={loading} style={btnPrimary}>
            {loading ? 'Approving…' : 'Approve Organization'}
          </button>
        </div>
      </div>
    </div>
  )
}

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
}
const modal: React.CSSProperties = {
  background: '#fff', borderRadius: 12, padding: 28, width: 440,
  maxWidth: '95vw', boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
}
const btnPrimary: React.CSSProperties = {
  background: '#16A34A', color: '#fff', border: 'none',
  padding: '8px 18px', borderRadius: 7, cursor: 'pointer', fontWeight: 600, fontSize: 14,
}
const btnSecondary: React.CSSProperties = {
  background: '#F3F4F6', color: '#374151', border: '1px solid #E5E7EB',
  padding: '8px 18px', borderRadius: 7, cursor: 'pointer', fontSize: 14,
}
