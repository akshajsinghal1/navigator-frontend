import type { NavigatorConfig, ViewDataResponse } from "../types/navigator";

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path);
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json() as Promise<T>;
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
};
