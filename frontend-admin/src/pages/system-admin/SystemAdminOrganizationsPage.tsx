import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../lib/api'
import type { MeResponse, OrgListItem, OrgListResponse, ApproveResponse, RejectResponse } from '../../types/systemAdmin'
import { SystemAdminLayout } from '../../components/system-admin/SystemAdminLayout'
import { InternalStatusBadge } from '../../components/system-admin/InternalStatusBadge'
import { ApproveOrganizationModal } from '../../components/system-admin/ApproveOrganizationModal'
import { RejectOrganizationModal } from '../../components/system-admin/RejectOrganizationModal'

const STATUS_MAP: Record<string, string> = {
  pending: 'pending_approval',
  approved: 'approved',
  rejected: 'rejected',
}

export function SystemAdminOrganizationsPage({ me }: { me: MeResponse }) {
  const { tab } = useParams<{ tab?: string }>()
  const navigate = useNavigate()
  const statusFilter = tab ? STATUS_MAP[tab] : undefined

  const [orgs, setOrgs] = useState<OrgListItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [approving, setApproving] = useState<OrgListItem | null>(null)
  const [rejecting, setRejecting] = useState<OrgListItem | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (statusFilter) params.set('status', statusFilter)
      if (search) params.set('search', search)
      const data = await api.get<OrgListResponse>(`/api/system-admin/organizations?${params}`)
      setOrgs(data.items)
      setTotal(data.pagination.total)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, search])

  useEffect(() => { load() }, [load])

  function showToast(msg: string) {
    setToast(msg)
    setTimeout(() => setToast(null), 4000)
  }

  function handleApproveSuccess(res: ApproveResponse) {
    setApproving(null)
    showToast(res.email_sent
      ? `Organization approved. Email sent to ${approving?.created_by.email}.`
      : `Organization approved, but email could not be sent. Please contact the user manually.`)
    load()
  }

  function handleRejectSuccess(res: RejectResponse) {
    setRejecting(null)
    showToast(res.email_sent
      ? `Organization rejected. Email sent to ${rejecting?.created_by.email}.`
      : `Organization rejected, but email could not be sent. Please contact the user manually.`)
    load()
  }

  const isAdmin = me.system_admin.role === 'system_admin'

  const emptyMessages: Record<string, string> = {
    pending: 'No pending organization requests.',
    approved: 'No approved organizations yet.',
    rejected: 'No rejected organizations.',
  }
  const emptyMsg = (tab && emptyMessages[tab]) || 'No organizations found.'

  return (
    <SystemAdminLayout me={me}>
      <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: '#1E293B' }}>
            {tab ? `${tab.charAt(0).toUpperCase() + tab.slice(1)} Organizations` : 'All Organizations'}
          </h1>
          {!loading && <p style={{ margin: '4px 0 0', fontSize: 13, color: '#6B7280' }}>{total} result{total !== 1 ? 's' : ''}</p>}
        </div>
        <input
          type="text"
          placeholder="Search by name, email, industry…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ padding: '8px 12px', border: '1px solid #D1D5DB', borderRadius: 7, fontSize: 14, width: 260 }}
        />
      </div>

      {loading ? (
        <p style={{ color: '#9CA3AF' }}>Loading…</p>
      ) : orgs.length === 0 ? (
        <div style={{ background: '#fff', borderRadius: 10, padding: 40, textAlign: 'center', border: '1px solid #E5E7EB' }}>
          <p style={{ color: '#9CA3AF', margin: 0 }}>{emptyMsg}</p>
        </div>
      ) : (
        <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #E5E7EB', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#F9FAFB' }}>
                {['Organization', 'Industry', 'Submitted By', 'Personas', 'Members', 'Status', 'Date', 'Actions'].map(h => (
                  <th key={h} style={th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orgs.map((org) => (
                <tr key={org.organization_id} style={{ borderTop: '1px solid #F3F4F6' }}>
                  <td style={td}>
                    <button
                      onClick={() => navigate(`/system-admin/organizations/${org.organization_id}/detail`)}
                      style={{ background: 'none', border: 'none', color: '#2563EB', cursor: 'pointer', fontWeight: 600, padding: 0, fontSize: 13 }}
                    >
                      {org.organization_name}
                    </button>
                  </td>
                  <td style={td}>{org.industry_name}</td>
                  <td style={td}>
                    <div>{org.created_by.name || '—'}</div>
                    <div style={{ color: '#9CA3AF', fontSize: 12 }}>{org.created_by.email}</div>
                  </td>
                  <td style={{ ...td, textAlign: 'center' }}>{org.persona_count}</td>
                  <td style={{ ...td, textAlign: 'center' }}>{org.member_count}</td>
                  <td style={td}><InternalStatusBadge status={org.status} /></td>
                  <td style={{ ...td, whiteSpace: 'nowrap' }}>{new Date(org.created_at).toLocaleDateString()}</td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <button onClick={() => navigate(`/system-admin/organizations/${org.organization_id}/detail`)} style={btnView}>View</button>
                      {isAdmin && org.status === 'pending_approval' && (
                        <>
                          <button onClick={() => setApproving(org)} style={btnApprove}>Approve</button>
                          <button onClick={() => setRejecting(org)} style={btnReject}>Reject</button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {approving && (
        <ApproveOrganizationModal org={approving} onClose={() => setApproving(null)} onSuccess={handleApproveSuccess} />
      )}
      {rejecting && (
        <RejectOrganizationModal org={rejecting} onClose={() => setRejecting(null)} onSuccess={handleRejectSuccess} />
      )}

      {toast && (
        <div style={{ position: 'fixed', bottom: 24, right: 24, background: '#1E293B', color: '#fff', padding: '12px 20px', borderRadius: 8, fontSize: 14, maxWidth: 400, boxShadow: '0 4px 16px rgba(0,0,0,0.2)', zIndex: 2000 }}>
          {toast}
        </div>
      )}
    </SystemAdminLayout>
  )
}

const th: React.CSSProperties = { textAlign: 'left', padding: '10px 14px', fontWeight: 600, color: '#6B7280', fontSize: 12, whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '12px 14px', color: '#374151', verticalAlign: 'top' }
const btnView: React.CSSProperties = { background: '#F1F5F9', border: 'none', color: '#334155', padding: '4px 10px', borderRadius: 5, cursor: 'pointer', fontSize: 12 }
const btnApprove: React.CSSProperties = { background: '#DCFCE7', border: 'none', color: '#166534', padding: '4px 10px', borderRadius: 5, cursor: 'pointer', fontSize: 12, fontWeight: 600 }
const btnReject: React.CSSProperties = { background: '#FEE2E2', border: 'none', color: '#991B1B', padding: '4px 10px', borderRadius: 5, cursor: 'pointer', fontSize: 12, fontWeight: 600 }
