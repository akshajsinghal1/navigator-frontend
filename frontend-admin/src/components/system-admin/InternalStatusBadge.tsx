import type { OrgStatus } from '../../types/systemAdmin'

const config: Record<string, { label: string; bg: string; text: string }> = {
  pending_approval: { label: 'Pending Approval', bg: '#FEF3C7', text: '#92400E' },
  approved: { label: 'Approved', bg: '#D1FAE5', text: '#065F46' },
  rejected: { label: 'Rejected', bg: '#FEE2E2', text: '#991B1B' },
  suspended: { label: 'Suspended', bg: '#E5E7EB', text: '#374151' },
  active: { label: 'Active', bg: '#D1FAE5', text: '#065F46' },
  deactivated: { label: 'Deactivated', bg: '#FEE2E2', text: '#991B1B' },
  system_admin: { label: 'Admin', bg: '#EDE9FE', text: '#5B21B6' },
  system_viewer: { label: 'Viewer', bg: '#DBEAFE', text: '#1E40AF' },
}

export function InternalStatusBadge({ status }: { status: string }) {
  const c = config[status] ?? { label: status, bg: '#F3F4F6', text: '#374151' }
  return (
    <span
      style={{
        backgroundColor: c.bg,
        color: c.text,
        padding: '2px 10px',
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      {c.label}
    </span>
  )
}
