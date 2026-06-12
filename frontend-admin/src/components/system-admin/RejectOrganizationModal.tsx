import { useState } from 'react'
import { api } from '../../lib/api'
import type { OrgListItem, RejectResponse } from '../../types/systemAdmin'

export function RejectOrganizationModal({
  org,
  onClose,
  onSuccess,
}: {
  org: OrgListItem
  onClose: () => void
  onSuccess: (res: RejectResponse) => void
}) {
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleReject() {
    setError(null)
    const trimmed = reason.trim()
    if (trimmed.length < 10) {
      setError('Reason must be at least 10 characters.')
      return
    }
    if (trimmed.length > 1000) {
      setError('Reason must be at most 1000 characters.')
      return
    }
    setLoading(true)
    try {
      const res = await api.patch<RejectResponse>(
        `/api/system-admin/organizations/${org.organization_id}/reject`,
        { rejection_reason: trimmed }
      )
      onSuccess(res)
    } catch (e: any) {
      setError(e?.detail?.message || 'Failed to reject organization.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={overlay}>
      <div style={modal}>
        <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>Reject Organization?</h2>
        <p style={{ color: '#6B7280', margin: '0 0 20px', fontSize: 14 }}>
          This will reject the organization request and notify the requester.
        </p>
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
            Rejection Reason <span style={{ color: '#DC2626' }}>*</span>
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={4}
            placeholder="Provide a reason (10–1000 characters)…"
            style={{
              width: '100%', boxSizing: 'border-box', padding: '8px 12px',
              border: '1px solid #D1D5DB', borderRadius: 7, fontSize: 14,
              resize: 'vertical', fontFamily: 'inherit',
            }}
          />
          <p style={{ fontSize: 12, color: '#9CA3AF', margin: '4px 0 0' }}>{reason.length} / 1000</p>
        </div>
        {error && <p style={{ color: '#DC2626', fontSize: 13, marginBottom: 12 }}>{error}</p>}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onClose} disabled={loading} style={btnSecondary}>Cancel</button>
          <button onClick={handleReject} disabled={loading} style={btnDanger}>
            {loading ? 'Rejecting…' : 'Reject Organization'}
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
  background: '#fff', borderRadius: 12, padding: 28, width: 480,
  maxWidth: '95vw', boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
}
const btnDanger: React.CSSProperties = {
  background: '#DC2626', color: '#fff', border: 'none',
  padding: '8px 18px', borderRadius: 7, cursor: 'pointer', fontWeight: 600, fontSize: 14,
}
const btnSecondary: React.CSSProperties = {
  background: '#F3F4F6', color: '#374151', border: '1px solid #E5E7EB',
  padding: '8px 18px', borderRadius: 7, cursor: 'pointer', fontSize: 14,
}
