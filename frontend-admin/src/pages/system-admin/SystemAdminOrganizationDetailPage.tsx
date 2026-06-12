import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../lib/api'
import type { MeResponse, OrgDetailResponse, OrgListItem, ApproveResponse, RejectResponse } from '../../types/systemAdmin'
import { SystemAdminLayout } from '../../components/system-admin/SystemAdminLayout'
import { InternalStatusBadge } from '../../components/system-admin/InternalStatusBadge'
import { AuditLogTable } from '../../components/system-admin/AuditLogTable'
import { ApproveOrganizationModal } from '../../components/system-admin/ApproveOrganizationModal'
import { RejectOrganizationModal } from '../../components/system-admin/RejectOrganizationModal'

export function SystemAdminOrganizationDetailPage({ me }: { me: MeResponse }) {
  const { organizationId } = useParams<{ organizationId: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<OrgDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [approving, setApproving] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<OrgDetailResponse>(`/api/system-admin/organizations/${organizationId}`)
      setData(res)
    } catch {
      setError('Organization not found.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [organizationId])

  function showToast(msg: string) {
    setToast(msg)
    setTimeout(() => setToast(null), 4000)
  }

  function handleApproveSuccess(res: ApproveResponse) {
    setApproving(false)
    showToast(res.email_sent ? 'Organization approved. Approval email sent.' : 'Organization approved, but email could not be sent.')
    load()
  }

  function handleRejectSuccess(res: RejectResponse) {
    setRejecting(false)
    showToast(res.email_sent ? 'Organization rejected. Rejection email sent.' : 'Organization rejected, but email could not be sent.')
    load()
  }

  const isAdmin = me.system_admin.role === 'system_admin'
  const isPending = data?.organization.status === 'pending_approval'

  const orgAsListItem = data ? {
    organization_id: data.organization.id,
    organization_name: data.organization.name,
    industry_name: data.organization.industry_name,
    status: data.organization.status,
    created_at: data.organization.created_at,
    created_by: data.created_by,
    persona_count: data.personas.length,
    member_count: data.members.length,
  } as OrgListItem : null

  if (loading) {
    return (
      <SystemAdminLayout me={me}>
        <p style={{ color: '#9CA3AF' }}>Loading…</p>
      </SystemAdminLayout>
    )
  }

  if (error || !data) {
    return (
      <SystemAdminLayout me={me}>
        <p style={{ color: '#DC2626' }}>{error || 'Not found.'}</p>
      </SystemAdminLayout>
    )
  }

  const { organization: org, created_by, personas, members, audit_logs } = data

  return (
    <SystemAdminLayout me={me}>
      <div style={{ maxWidth: 860 }}>
        {/* Back */}
        <button onClick={() => navigate(-1)} style={backBtn}>← Back</button>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 28, flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ margin: '0 0 6px', fontSize: 22, fontWeight: 700, color: '#1E293B' }}>{org.name}</h1>
            <InternalStatusBadge status={org.status} />
          </div>
          {isAdmin && isPending && (
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => setApproving(true)} style={btnApprove}>Approve Organization</button>
              <button onClick={() => setRejecting(true)} style={btnReject}>Reject Organization</button>
            </div>
          )}
          {!isPending && (
            <div style={{ fontSize: 13, color: '#6B7280' }}>
              {org.status === 'approved' && org.approved_at && (
                <span>Approved on {new Date(org.approved_at).toLocaleString()}</span>
              )}
              {org.status === 'rejected' && (
                <span>No approval actions available.</span>
              )}
            </div>
          )}
        </div>

        {/* Org Summary */}
        <Section title="Organization Summary">
          <Grid>
            <Field label="Organization Name" value={org.name} />
            <Field label="Industry" value={org.industry_name} />
            <Field label="Status"><InternalStatusBadge status={org.status} /></Field>
            <Field label="Created At" value={new Date(org.created_at).toLocaleString()} />
            {org.approved_at && <Field label="Approved At" value={new Date(org.approved_at).toLocaleString()} />}
            {org.rejection_reason && (
              <Field label="Rejection Reason" value={org.rejection_reason} />
            )}
          </Grid>
        </Section>

        {/* Submitted By */}
        <Section title="Submitted By">
          <Grid>
            <Field label="Name" value={created_by.name || '—'} />
            <Field label="Email" value={created_by.email} />
            <Field label="User ID" value={created_by.user_id} mono />
          </Grid>
        </Section>

        {/* Personas */}
        <Section title={`Personas (${personas.length})`}>
          {personas.length === 0 ? (
            <p style={{ color: '#9CA3AF', fontSize: 14 }}>No personas defined.</p>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {personas.map((p) => (
                <span key={p.id} style={{ background: '#EDE9FE', color: '#5B21B6', padding: '4px 12px', borderRadius: 20, fontSize: 13, fontWeight: 500 }}>
                  {p.name}
                </span>
              ))}
            </div>
          )}
        </Section>

        {/* Members */}
        <Section title={`Members (${members.length})`}>
          {members.length === 0 ? (
            <p style={{ color: '#9CA3AF', fontSize: 14 }}>No members.</p>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#F9FAFB' }}>
                  {['Name', 'Email', 'Permission', 'Status', 'Joined'].map(h => (
                    <th key={h} style={th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.membership_id} style={{ borderTop: '1px solid #F3F4F6' }}>
                    <td style={td}>{m.name || '—'}</td>
                    <td style={td}>{m.email}</td>
                    <td style={td}><span style={{ textTransform: 'capitalize' }}>{m.permission_level}</span></td>
                    <td style={td}><InternalStatusBadge status={m.status} /></td>
                    <td style={td}>{new Date(m.joined_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        {/* Audit Logs */}
        <Section title="Audit History">
          <AuditLogTable logs={audit_logs} />
        </Section>
      </div>

      {approving && orgAsListItem && (
        <ApproveOrganizationModal org={orgAsListItem} onClose={() => setApproving(false)} onSuccess={handleApproveSuccess} />
      )}
      {rejecting && orgAsListItem && (
        <RejectOrganizationModal org={orgAsListItem} onClose={() => setRejecting(false)} onSuccess={handleRejectSuccess} />
      )}

      {toast && (
        <div style={{ position: 'fixed', bottom: 24, right: 24, background: '#1E293B', color: '#fff', padding: '12px 20px', borderRadius: 8, fontSize: 14, maxWidth: 400, boxShadow: '0 4px 16px rgba(0,0,0,0.2)', zIndex: 2000 }}>
          {toast}
        </div>
      )}
    </SystemAdminLayout>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: '#fff', border: '1px solid #E5E7EB', borderRadius: 10, padding: 24, marginBottom: 20 }}>
      <h3 style={{ margin: '0 0 16px', fontSize: 15, fontWeight: 700, color: '#1E293B' }}>{title}</h3>
      {children}
    </div>
  )
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '12px 24px' }}>{children}</div>
}

function Field({ label, value, children, mono }: { label: string; value?: string; children?: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <p style={{ margin: '0 0 2px', fontSize: 11, fontWeight: 600, color: '#9CA3AF', letterSpacing: 0.5 }}>{label.toUpperCase()}</p>
      {children || <p style={{ margin: 0, fontSize: 14, color: '#1E293B', fontFamily: mono ? 'monospace' : undefined, wordBreak: 'break-all' }}>{value || '—'}</p>}
    </div>
  )
}

const th: React.CSSProperties = { textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: '#6B7280', fontSize: 12 }
const td: React.CSSProperties = { padding: '10px 12px', color: '#374151' }
const backBtn: React.CSSProperties = { background: 'none', border: 'none', color: '#6B7280', cursor: 'pointer', fontSize: 13, padding: '0 0 16px', display: 'block' }
const btnApprove: React.CSSProperties = { background: '#16A34A', color: '#fff', border: 'none', padding: '9px 18px', borderRadius: 7, cursor: 'pointer', fontWeight: 600, fontSize: 14 }
const btnReject: React.CSSProperties = { background: '#DC2626', color: '#fff', border: 'none', padding: '9px 18px', borderRadius: 7, cursor: 'pointer', fontWeight: 600, fontSize: 14 }
