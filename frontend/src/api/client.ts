import type { NavigatorConfig, ViewDataResponse } from "../types/navigator";

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path);
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

// ── Inventory response type ───────────────────────────────────────────────────

export interface InventoryPersona {
  role:        string;
  focus_areas: string[];
  kpi_count:   number;
  kpi_names:   string[];
}

export interface InventoryResponse {
  company_id:    string;
  workbook_name: string;
  generated_at:  string;
  objective:     string;
  views:         { name: string; updated_at?: string }[];
  view_count:    number;
  datasources:   { name: string; field_count: number | null }[];
  total_fields:  number;
  parameters:    { name: string; current_value?: string; data_type?: string }[];
  total_kpis:    number;
  persona_count: number;
  personas:      InventoryPersona[];
}

export const api = {
  // ── Navigator Intelligence Config ────────────────────────────────────────────
  // GET /dashboard/{workbook}  — returns DashboardConfigResponse wrapping the config.
  // We unwrap .config to get the raw IntelligenceConfig the frontend expects.
  intelligenceConfig: async (workbook: string): Promise<NavigatorConfig> => {
    const res = await get<{ config: NavigatorConfig }>(`/dashboard/${encodeURIComponent(workbook)}`);
    return res.config;
  },

  // GET /viewdata  — live Tableau view data for chart rendering.
  // The config stores only L1 aggregates; charts fetch fresh rows here.
  viewData: (workbook: string, view: string) =>
    get<ViewDataResponse>(
      `/viewdata?workbook=${encodeURIComponent(workbook)}&view=${encodeURIComponent(view)}`
    ),

  // GET /inventory/{company_id} — what Navigator read from Tableau.
  // Powers the Inventory screen shown after the pipeline completes.
  inventory: (companyId: string) =>
    get<InventoryResponse>(`/inventory/${encodeURIComponent(companyId)}`),
};
