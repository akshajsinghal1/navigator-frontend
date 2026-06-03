// ─── InventoryScreen ─────────────────────────────────────────────────────────
// Screen 3 of 4: shown after the pipeline completes, before the dashboard.
//
// "After I connect to Tableau, after I go through the status of loading,
//  I believe there should be an inventory screen — a screen that confirms
//  what you read out of Tableau." — Richard (MVP update call)
//
// Read-only — no editing. Just transparency: here's what we found,
// here's what we built from it. Click Continue to see the dashboard.

import { useEffect, useState } from "react";
import { useChartTheme } from "../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT } from "../components/charts/chartTheme";
import { api } from "../api/client";
import type { InventoryResponse } from "../api/client";

interface Props {
  companyId:  string;
  workbook:   string;
  onContinue: () => void;
}

// ── Stat tile ─────────────────────────────────────────────────────────────────

function StatTile({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  const { palette } = useChartTheme();
  return (
    <div style={{
      background: palette.bg,
      border: `1px solid ${palette.line}`,
      borderRadius: 8,
      padding: "18px 20px",
      display: "flex",
      flexDirection: "column",
      gap: 4,
    }}>
      <span style={{
        fontFamily: CHART_NUM_FONT,
        fontSize: 28,
        fontWeight: 600,
        color: palette.accent,
        letterSpacing: "-0.02em",
        lineHeight: 1,
      }}>
        {value}
      </span>
      <span style={{ fontFamily: CHART_FONT, fontSize: 13, fontWeight: 600, color: palette.ink }}>
        {label}
      </span>
      {sub && (
        <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink3 }}>
          {sub}
        </span>
      )}
    </div>
  );
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionLabel({ title }: { title: string }) {
  const { palette } = useChartTheme();
  return (
    <div style={{
      fontFamily: CHART_NUM_FONT,
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      color: palette.ink4,
      marginBottom: 10,
      paddingBottom: 8,
      borderBottom: `1px solid ${palette.line}`,
    }}>
      {title}
    </div>
  );
}

// ── Pill chip ─────────────────────────────────────────────────────────────────

function Chip({ label, accent }: { label: string; accent?: boolean }) {
  const { palette } = useChartTheme();
  return (
    <span style={{
      fontFamily: CHART_FONT,
      fontSize: 12,
      padding: "3px 10px",
      borderRadius: 20,
      background: accent ? `${palette.accent}18` : palette.bg2,
      border: `1px solid ${accent ? `${palette.accent}40` : palette.line}`,
      color: accent ? palette.accent : palette.ink2,
      whiteSpace: "nowrap",
    }}>
      {label}
    </span>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export function InventoryScreen({ companyId, workbook, onContinue }: Props) {
  const { palette } = useChartTheme();
  const [inv, setInv]       = useState<InventoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);

  useEffect(() => {
    api.inventory(companyId)
      .then((data) => {
        setInv(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(String(err));
        setLoading(false);
      });
  }, [companyId]);

  // ── Loading ──────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={{
        minHeight: "100vh", background: palette.bg1,
        display: "flex", alignItems: "center", justifyContent: "center",
        flexDirection: "column", gap: 12,
      }}>
        <div style={{
          width: 32, height: 32,
          border: `2px solid ${palette.line2}`,
          borderTop: `2px solid ${palette.accent}`,
          borderRadius: "50%",
          animation: "spin 0.8s linear infinite",
        }} />
        <span style={{ fontFamily: CHART_FONT, fontSize: 13, color: palette.ink3 }}>
          Loading inventory…
        </span>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  // ── Error — still let them continue ─────────────────────────────────────
  if (error || !inv) {
    return (
      <div style={{
        minHeight: "100vh", background: palette.bg1,
        display: "flex", alignItems: "center", justifyContent: "center",
        flexDirection: "column", gap: 20, padding: 40,
      }}>
        <span style={{ fontFamily: CHART_FONT, fontSize: 16, color: palette.ink }}>
          Pipeline completed for <strong>{workbook}</strong>
        </span>
        <span style={{ fontFamily: CHART_FONT, fontSize: 13, color: palette.ink3 }}>
          Inventory details unavailable. You can still continue to the dashboard.
        </span>
        <button
          type="button"
          onClick={onContinue}
          style={{
            background: palette.accent, color: palette.bg,
            border: "none", borderRadius: 6, padding: "12px 28px",
            fontFamily: CHART_FONT, fontSize: 14, fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Continue to Dashboard →
        </button>
      </div>
    );
  }

  const generatedDate = inv.generated_at
    ? new Date(inv.generated_at).toLocaleString()
    : "just now";

  return (
    <div style={{ minHeight: "100vh", background: palette.bg1, display: "flex", flexDirection: "column" }}>

      {/* Header */}
      <header style={{
        padding: "16px 40px",
        borderBottom: `1px solid ${palette.line}`,
        background: palette.bg,
        display: "flex", alignItems: "center", gap: 14,
      }}>
        <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
          <circle cx="11" cy="11" r="5" fill={palette.accent} opacity="0.9" />
          <circle cx="11" cy="11" r="9" stroke={palette.accent} strokeWidth="1.5" opacity="0.35" />
          <circle cx="11" cy="11" r="3" fill={palette.bg} />
        </svg>
        <span style={{ fontFamily: CHART_FONT, fontWeight: 700, fontSize: 14, color: palette.ink }}>
          Navigator
        </span>
        <span style={{ width: 1, height: 16, background: palette.line2 }} />
        <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink3 }}>
          {inv.workbook_name}
        </span>
        <span style={{
          marginLeft: "auto",
          fontFamily: CHART_NUM_FONT, fontSize: 11,
          color: palette.green,
          background: `${palette.green}15`,
          border: `1px solid ${palette.green}35`,
          borderRadius: 4, padding: "2px 8px",
        }}>
          ✓ Pipeline complete
        </span>
      </header>

      {/* Main */}
      <main style={{ flex: 1, padding: "32px 40px 60px", maxWidth: 900, margin: "0 auto", width: "100%" }}>

        {/* Title */}
        <div style={{ marginBottom: 32 }}>
          <h1 style={{
            fontFamily: CHART_FONT, fontSize: 24, fontWeight: 700,
            color: palette.ink, margin: "0 0 8px", letterSpacing: "-0.01em",
          }}>
            Here's what we found in Tableau
          </h1>
          <p style={{ fontFamily: CHART_FONT, fontSize: 14, color: palette.ink3, margin: 0 }}>
            Navigator read your workbook and built an intelligence layer from this data.
            Generated {generatedDate}.
          </p>
        </div>

        {/* Stats row */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 12,
          marginBottom: 32,
        }}>
          <StatTile label="Views / Sheets" value={inv.view_count || inv.views.length} sub="discovered in Tableau" />
          <StatTile label="Data Sources"   value={inv.datasources.length} sub={`${inv.total_fields} total fields`} />
          <StatTile label="KPIs Generated" value={inv.total_kpis} sub="AI-selected and computed" />
          <StatTile label="Personas"        value={inv.persona_count} sub="role-based dashboards" />
        </div>

        {/* Objective */}
        {inv.objective && (
          <div style={{
            background: `${palette.accent}10`,
            border: `1px solid ${palette.accent}30`,
            borderLeft: `3px solid ${palette.accent}`,
            borderRadius: 6,
            padding: "14px 18px",
            marginBottom: 28,
          }}>
            <span style={{
              fontFamily: CHART_NUM_FONT, fontSize: 10, fontWeight: 700,
              letterSpacing: "0.08em", textTransform: "uppercase",
              color: palette.accent, display: "block", marginBottom: 6,
            }}>
              Business Objective
            </span>
            <span style={{ fontFamily: CHART_FONT, fontSize: 14, color: palette.ink, lineHeight: 1.5 }}>
              {inv.objective}
            </span>
          </div>
        )}

        {/* Two-column layout */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 28 }}>

          {/* Views */}
          <div>
            <SectionLabel title={`Views / Sheets (${inv.views.length})`} />
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {inv.views.map((v) => (
                <div key={v.name} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "8px 12px",
                  background: palette.bg,
                  border: `1px solid ${palette.line}`,
                  borderRadius: 5,
                }}>
                  <span style={{ fontFamily: CHART_FONT, fontSize: 13, color: palette.ink, fontWeight: 500 }}>
                    {v.name}
                  </span>
                  {v.updated_at && (
                    <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 10, color: palette.ink4 }}>
                      {new Date(v.updated_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Data sources */}
          <div>
            <SectionLabel title={`Data Sources (${inv.datasources.length})`} />
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {inv.datasources.map((ds) => (
                <div key={ds.name} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "8px 12px",
                  background: palette.bg,
                  border: `1px solid ${palette.line}`,
                  borderRadius: 5,
                }}>
                  <span style={{ fontFamily: CHART_FONT, fontSize: 13, color: palette.ink, fontWeight: 500 }}>
                    {ds.name}
                  </span>
                  {ds.field_count != null && (
                    <span style={{
                      fontFamily: CHART_NUM_FONT, fontSize: 11,
                      color: palette.ink3,
                      background: palette.bg2,
                      border: `1px solid ${palette.line}`,
                      borderRadius: 4, padding: "1px 7px",
                    }}>
                      {ds.field_count} fields
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Personas + KPIs */}
        <div style={{ marginBottom: 32 }}>
          <SectionLabel title={`Personas & KPIs Generated (${inv.persona_count} personas · ${inv.total_kpis} KPIs)`} />
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {inv.personas.map((p) => (
              <div key={p.role} style={{
                background: palette.bg,
                border: `1px solid ${palette.line}`,
                borderRadius: 6,
                padding: "14px 16px",
              }}>
                <div style={{
                  display: "flex", alignItems: "center",
                  justifyContent: "space-between", marginBottom: 10,
                }}>
                  <span style={{ fontFamily: CHART_FONT, fontSize: 14, fontWeight: 600, color: palette.ink }}>
                    {p.role}
                  </span>
                  <span style={{
                    fontFamily: CHART_NUM_FONT, fontSize: 11,
                    color: palette.accent, fontWeight: 700,
                  }}>
                    {p.kpi_count} KPIs
                  </span>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                  {p.focus_areas.map((area) => (
                    <Chip key={area} label={area} />
                  ))}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {p.kpi_names.map((name) => (
                    <Chip key={name} label={name} accent />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Parameters (if any) */}
        {inv.parameters.length > 0 && (
          <div style={{ marginBottom: 32 }}>
            <SectionLabel title={`Tableau Parameters (${inv.parameters.length})`} />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {inv.parameters.map((p) => (
                <div key={p.name} style={{
                  background: palette.bg,
                  border: `1px solid ${palette.line}`,
                  borderRadius: 5,
                  padding: "6px 12px",
                  display: "flex", alignItems: "center", gap: 8,
                }}>
                  <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink, fontWeight: 500 }}>
                    {p.name}
                  </span>
                  {p.current_value && (
                    <span style={{
                      fontFamily: CHART_NUM_FONT, fontSize: 11, color: palette.ink3,
                    }}>
                      = {p.current_value}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* CTA */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          borderTop: `1px solid ${palette.line}`,
          paddingTop: 24,
        }}>
          <span style={{ fontFamily: CHART_FONT, fontSize: 13, color: palette.ink3 }}>
            Your AI-powered intelligence dashboard is ready.
          </span>
          <button
            type="button"
            onClick={onContinue}
            style={{
              background: palette.accent,
              color: palette.bg,
              border: "none",
              borderRadius: 6,
              padding: "12px 32px",
              fontFamily: CHART_FONT,
              fontSize: 14,
              fontWeight: 700,
              cursor: "pointer",
              letterSpacing: "-0.01em",
              transition: "opacity 0.15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.85")}
            onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
          >
            Continue to Dashboard →
          </button>
        </div>

      </main>
    </div>
  );
}
