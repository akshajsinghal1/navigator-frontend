/** Strip orchestrator chart-intent prefix from KPI descriptions for display. */
export function formatKpiDescription(desc: string | null | undefined): string {
  if (!desc) return "";
  return desc
    .replace(/^Chart:\s*[\w_]+\s*[-–—]\s*/i, "")
    .replace(/^Chart:\s*[\w_]+\s*/i, "")
    .trim();
}
