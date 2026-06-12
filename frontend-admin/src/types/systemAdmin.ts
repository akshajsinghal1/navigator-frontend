export interface SystemAdminUser {
  id: string
  email: string
  name?: string
}

export interface SystemAdminInfo {
  role: 'system_admin' | 'system_viewer'
  is_active: boolean
  permissions: string[]
}

export interface MeResponse {
  user: SystemAdminUser
  system_admin: SystemAdminInfo
}

export interface CreatedBy {
  user_id: string
  name?: string
  email: string
}

export interface OrgListItem {
  organization_id: string
  organization_name: string
  industry_name: string
  status: OrgStatus
  created_at: string
  created_by: CreatedBy
  persona_count: number
  member_count: number
}

export interface Pagination {
  page: number
  page_size: number
  total: number
}

export interface OrgListResponse {
  items: OrgListItem[]
  pagination: Pagination
}

export type OrgStatus = 'pending_approval' | 'approved' | 'rejected' | 'suspended'

export interface Persona {
  id: string
  name: string
  is_active: boolean
  created_at?: string
}

export interface Member {
  membership_id: string
  name?: string
  email: string
  permission_level: string
  status: string
  joined_at: string
}

export interface AuditLog {
  id: string
  action: string
  actor_email?: string
  created_at: string
  metadata: Record<string, unknown>
}

export interface OrgDetail {
  id: string
  name: string
  industry_name: string
  status: OrgStatus
  created_at: string
  approved_at?: string
  rejection_reason?: string
}

export interface OrgDetailResponse {
  organization: OrgDetail
  created_by: CreatedBy
  personas: Persona[]
  members: Member[]
  audit_logs: AuditLog[]
}

export interface ApproveResponse {
  organization: { id: string; name: string; status: string; approved_at?: string }
  email_sent: boolean
  email_error?: string
}

export interface RejectResponse {
  organization: { id: string; name: string; status: string; rejection_reason: string }
  email_sent: boolean
  email_error?: string
}

export interface StatsResponse {
  pending_count: number
  approved_count: number
  rejected_count: number
  organizations_created_last_7_days: number
}
