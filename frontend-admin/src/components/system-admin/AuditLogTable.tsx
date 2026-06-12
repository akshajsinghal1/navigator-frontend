import type { AuditLog } from '../../types/systemAdmin'

export function AuditLogTable({ logs }: { logs: AuditLog[] }) {
  if (logs.length === 0) {
    return <p style={{ color: '#9CA3AF', fontSize: 14 }}>No audit logs yet.</p>
  }
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ background: '#F9FAFB' }}>
          {['Action', 'Actor', 'Date'].map((h) => (
            <th key={h} style={th}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {logs.map((log) => (
          <tr key={log.id} style={{ borderBottom: '1px solid #F3F4F6' }}>
            <td style={td}><code style={{ fontSize: 12 }}>{log.action}</code></td>
            <td style={td}>{log.actor_email || '—'}</td>
            <td style={td}>{new Date(log.created_at).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const th: React.CSSProperties = {
  textAlign: 'left', padding: '8px 12px', fontWeight: 600,
  color: '#6B7280', borderBottom: '1px solid #E5E7EB',
}
const td: React.CSSProperties = {
  padding: '8px 12px', color: '#374151', verticalAlign: 'top',
}
