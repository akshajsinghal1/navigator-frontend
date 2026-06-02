// ─── Screen 2: Pipeline Progress ─────────────────────────────────────────────
// Polls GET /api/pipeline/{run_id}/status every 2s.
// Once the schema extraction step finishes, also polls /api/inventory to show
// a live preview of what was read from Tableau — while agents keep running.
//
// Layout:
//   Left column  — step-by-step progress (always visible)
//   Right column — inventory preview (appears after schema extraction)

import { useEffect, useRef, useState } from "react";
import { useChartTheme } from "../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT } from "../components/charts/chartTheme";
import { api } from "../api/client";
import type { InventoryResponse } from "../api/client";
import type { ConnectResult } from "./ConnectScreen";

// ── Types ─────────────────────────────────────────────────────────────────────

interface StatusMessage {
  time:    string;
  agent:   string;
  message: string;
  level:   string;
}

interface PipelineStatus {
  run_id:       string;
  company_id:   string;
  workbook:     string;
  status:       "queued" | "running" | "completed" | "failed";
  stage:        string;
  progress_pct: number;
  error:        string | null;
  started_at:   string | null;
  completed_at: string | null;
  messages:     StatusMessage[];
}

// ── Pipeline steps ────────────────────────────────────────────────────────────

const STEPS = [
  { stage: "connecting",           label: "Connecting to Tableau"          },
  { stage: "inventory_extraction", label: "Reading workbook schema"        },
  { stage: "eda",                  label: "Analysing fields & data"        },
  { stage: "orchestrator",         label: "Designing KPIs & personas"      },
  { stage: "domain_agents",        label: "Fetching data for each KPI"     },
  { stage: "chart_agents",         label: "Generating chart specs"         },
  { stage: "assembling",           label: "Assembling Intelligence Config"  },
];

const STAGE_ORDER = STEPS.map((s) => s.stage);

function stageIndex(stage: string): number {
  const idx = STAGE_ORDER.indexOf(stage);
  return idx === -1 ? -1 : idx;
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  runId:   string;
  runInfo: ConnectResult;
  onDone:  (companyId: string) => void;
  onRetry: () => void;
}

type Palette = ReturnType<typeof import("../components/charts/chartTheme").getChartPalette>;

// ── Step list ─────────────────────────────────────────────────────────────────

function StepList({ status, palette }: { status: PipelineStatus | null; palette: Palette }) {
  const currentIdx = status
    ? status.status === "completed" ? STEPS.length : stageIndex(status.stage)
    : -1;

  const latestMsg = status?.messages?.length
    ? status.messages[status.messages.length - 1].message
    : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {STEPS.map((step, i) => {
        const done    = i < currentIdx || status?.status === "completed";
        const active  = i === currentIdx && status?.status === "running";
        const dotColor   = done ? palette.green : active ? palette.accent : palette.line2;
        const labelColor = done ? palette.ink3  : active ? palette.ink   : palette.ink4;

        return (
          <div key={step.stage} style={{
            display: "flex", alignItems: "flex-start", gap: 14,
            padding: "10px 0",
            borderBottom: i < STEPS.length - 1 ? `1px solid ${palette.line}` : "none",
          }}>
            <div style={{ paddingTop: 2 }}>
              {done ? (
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <circle cx="9" cy="9" r="9" fill={palette.green} opacity="0.15" />
                  <circle cx="9" cy="9" r="6" fill={palette.green} />
                  <polyline points="5.5,9 7.5,11 12.5,6.5" stroke="white" strokeWidth="1.6"
                    strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : active ? (
                <div style={{
                  width: 18, height: 18, borderRadius: "50%",
                  border: `2.5px solid ${palette.accent}`, borderTopColor: "transparent",
                  animation: "spin 0.75s linear infinite", boxSizing: "border-box",
                }} />
              ) : (
                <div style={{
                  width: 18, height: 18, borderRadius: "50%",
                  border: `2px solid ${dotColor}`, boxSizing: "border-box",
                }} />
              )}
            </div>

            <div style={{ flex: 1, minWidth: 0, paddingTop: 1 }}>
              <div style={{
                fontFamily: CHART_FONT, fontSize: 13,
                fontWeight: active ? 600 : 400,
                color: labelColor, lineHeight: 1.4,
              }}>
                {step.label}
              </div>
              {active && latestMsg && (
                <div style={{
                  fontFamily: CHART_NUM_FONT, fontSize: 11, color: palette.ink4,
                  marginTop: 3, lineHeight: 1.4,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {latestMsg}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Inventory preview panel ───────────────────────────────────────────────────
// Appears on the right once schema extraction finishes.

function InventoryPreview({ inv, palette, isComplete }: {
  inv: InventoryResponse;
  palette: Palette;
  isComplete: boolean;
}) {
  return (
    <div style={{
      background: palette.bg1,
      border: `1px solid ${palette.line}`,
      borderRadius: 8,
      padding: "20px",
      animation: "fadeSlideIn 0.4s ease both",
      display: "flex", flexDirection: "column", gap: 18,
    }}>
      {/* Header */}
      <div>
        <div style={{
          fontFamily: CHART_NUM_FONT, fontSize: 10, fontWeight: 700,
          letterSpacing: "0.08em", textTransform: "uppercase",
          color: palette.accent, marginBottom: 6,
        }}>
          What we found in Tableau
        </div>
        <div style={{
          fontFamily: CHART_FONT, fontSize: 15, fontWeight: 600,
          color: palette.ink, marginBottom: 2,
        }}>
          {inv.workbook_name}
        </div>
        {inv.objective && (
          <div style={{
            fontFamily: CHART_FONT, fontSize: 12, color: palette.ink3,
            lineHeight: 1.4, marginTop: 4,
          }}>
            {inv.objective}
          </div>
        )}
      </div>

      {/* Stats row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {[
          { n: inv.view_count || inv.views.length, label: "Views" },
          { n: inv.datasources.length,             label: "Data Sources" },
          { n: inv.total_fields || "—",            label: "Fields" },
          { n: inv.parameters.length,              label: "Parameters" },
        ].map(({ n, label }) => (
          <div key={label} style={{
            background: palette.bg2, border: `1px solid ${palette.line}`,
            borderRadius: 6, padding: "10px 12px",
          }}>
            <div style={{
              fontFamily: CHART_NUM_FONT, fontSize: 22, fontWeight: 600,
              color: palette.accent, lineHeight: 1, marginBottom: 2,
            }}>
              {n}
            </div>
            <div style={{ fontFamily: CHART_FONT, fontSize: 11, color: palette.ink3 }}>
              {label}
            </div>
          </div>
        ))}
      </div>

      {/* Views list */}
      {inv.views.length > 0 && (
        <div>
          <div style={{
            fontFamily: CHART_NUM_FONT, fontSize: 10, fontWeight: 700,
            letterSpacing: "0.06em", textTransform: "uppercase",
            color: palette.ink4, marginBottom: 8,
          }}>
            Views / Sheets
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {inv.views.map((v) => (
              <span key={v.name} style={{
                fontFamily: CHART_FONT, fontSize: 11,
                padding: "3px 9px", borderRadius: 4,
                background: palette.bg2, border: `1px solid ${palette.line}`,
                color: palette.ink2,
              }}>
                {v.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Data sources */}
      {inv.datasources.length > 0 && (
        <div>
          <div style={{
            fontFamily: CHART_NUM_FONT, fontSize: 10, fontWeight: 700,
            letterSpacing: "0.06em", textTransform: "uppercase",
            color: palette.ink4, marginBottom: 8,
          }}>
            Data Sources
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {inv.datasources.map((ds) => (
              <div key={ds.name} style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "6px 10px",
                background: palette.bg2, border: `1px solid ${palette.line}`,
                borderRadius: 5,
              }}>
                <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink, fontWeight: 500 }}>
                  {ds.name}
                </span>
                {ds.field_count != null && (
                  <span style={{
                    fontFamily: CHART_NUM_FONT, fontSize: 10, color: palette.ink4,
                  }}>
                    {ds.field_count} fields
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Personas (appear once orchestrator runs) */}
      {inv.personas.length > 0 && (
        <div>
          <div style={{
            fontFamily: CHART_NUM_FONT, fontSize: 10, fontWeight: 700,
            letterSpacing: "0.06em", textTransform: "uppercase",
            color: palette.ink4, marginBottom: 8,
          }}>
            {isComplete ? `${inv.persona_count} Personas · ${inv.total_kpis} KPIs Generated` : "Personas being designed…"}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {inv.personas.map((p) => (
              <div key={p.role} style={{
                padding: "8px 10px",
                background: palette.bg2, border: `1px solid ${palette.line}`,
                borderRadius: 5,
              }}>
                <div style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  marginBottom: 4,
                }}>
                  <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink, fontWeight: 600 }}>
                    {p.role}
                  </span>
                  <span style={{
                    fontFamily: CHART_NUM_FONT, fontSize: 10, fontWeight: 700,
                    color: palette.accent,
                  }}>
                    {p.kpi_count} KPIs
                  </span>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {p.kpi_names.slice(0, 4).map((name) => (
                    <span key={name} style={{
                      fontFamily: CHART_FONT, fontSize: 10,
                      padding: "1px 7px", borderRadius: 3,
                      background: `${palette.accent}14`,
                      border: `1px solid ${palette.accent}30`,
                      color: palette.accent,
                    }}>
                      {name}
                    </span>
                  ))}
                  {p.kpi_names.length > 4 && (
                    <span style={{
                      fontFamily: CHART_NUM_FONT, fontSize: 10, color: palette.ink4,
                      alignSelf: "center",
                    }}>
                      +{p.kpi_names.length - 4} more
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export function PipelineScreen({ runId, runInfo, onDone, onRetry }: Props) {
  const { palette } = useChartTheme();
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const [inv, setInv]       = useState<InventoryResponse | null>(null);
  const doneRef             = useRef(false);
  const companyIdRef        = useRef<string>("");

  // Poll pipeline status every 2s
  // eslint-disable-next-line react-doctor/no-fetch-in-effect
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const res  = await fetch(`/api/pipeline/${runId}/status`);
        if (!res.ok) throw new Error(`Status ${res.status}`);
        const data: PipelineStatus = await res.json();
        setStatus(data);

        // Store company_id for inventory polling
        if (data.company_id) companyIdRef.current = data.company_id;

        if (data.status === "completed" && !doneRef.current) {
          doneRef.current = true;
          setTimeout(() => onDone(data.company_id), 1200);
          return;
        }

        if (data.status === "failed") {
          setError(data.error ?? "Pipeline failed");
          return;
        }
      } catch (err) {
        setError(String(err));
        return;
      }

      timer = setTimeout(poll, 2000);
    }

    poll();
    return () => clearTimeout(timer);
  }, [runId, onDone]);

  // Poll inventory every 8s once schema extraction has started
  // inventory_extraction is step index 1 — once past it, data should exist
  // eslint-disable-next-line react-doctor/no-fetch-in-effect
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let running = true;

    async function pollInventory() {
      const cid = companyIdRef.current;
      if (!cid) {
        // Not yet known — try again soon
        if (running) timer = setTimeout(pollInventory, 3000);
        return;
      }

      const currentStage = status?.stage ?? "";
      const idx = stageIndex(currentStage);
      // Only start fetching once inventory_extraction has begun (step 1+)
      // or pipeline is complete
      if (idx < 1 && status?.status !== "completed") {
        if (running) timer = setTimeout(pollInventory, 3000);
        return;
      }

      try {
        const data = await api.inventory(cid);
        setInv(data);
      } catch {
        // Not ready yet — silently retry
      }

      if (running) timer = setTimeout(pollInventory, 8000);
    }

    pollInventory();
    return () => { running = false; clearTimeout(timer); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.stage, status?.status]);

  const pct        = status?.progress_pct ?? 0;
  const isComplete = status?.status === "completed";
  const isFailed   = status?.status === "failed";
  const showInv    = inv !== null;

  return (
    <div style={{ minHeight: "100vh", background: palette.bg, display: "flex", flexDirection: "column" }}>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeSlideIn {
          from { opacity: 0; transform: translateX(12px); }
          to   { opacity: 1; transform: translateX(0); }
        }
      `}</style>

      {/* Header */}
      <header style={{
        padding: "16px 40px",
        borderBottom: `1px solid ${palette.line}`,
        display: "flex", alignItems: "center", gap: 14,
        background: palette.bg1,
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
        <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink3 }}>
          {runInfo.workbook}
        </span>
        <span style={{ marginLeft: "auto", fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink4 }}>
          run {runId.slice(0, 8)}
        </span>
      </header>

      {/* Main — two-column when inventory is available */}
      <main style={{
        flex: 1,
        padding: "40px",
        margin: "0 auto",
        width: "100%",
        maxWidth: showInv ? 1000 : 560,
        display: "grid",
        gridTemplateColumns: showInv ? "1fr 1fr" : "1fr",
        gap: 32,
        alignItems: "start",
        transition: "max-width 0.4s ease",
      }}>

        {/* Left — progress */}
        <div>
          {/* Title */}
          <div style={{ marginBottom: 28 }}>
            <h1 style={{
              fontFamily: CHART_FONT, fontSize: 22, fontWeight: 600,
              color: palette.ink, margin: "0 0 6px",
            }}>
              {isComplete ? "Intelligence Config ready ✓"
                : isFailed  ? "Pipeline failed"
                : "Generating Intelligence Config…"}
            </h1>
            <p style={{ fontFamily: CHART_FONT, fontSize: 13, color: palette.ink3, margin: 0 }}>
              {isComplete ? "Redirecting to your dashboard…"
                : isFailed  ? "Check the error below and try again"
                : "Analysing your Tableau workbook — this takes 3–5 minutes"}
            </p>
          </div>

          {/* Progress bar */}
          <div style={{
            height: 5, background: palette.bg3, borderRadius: 3,
            marginBottom: 28, overflow: "hidden",
          }}>
            <div style={{
              height: "100%", width: "100%",
              background: isFailed ? palette.red : isComplete ? palette.green : palette.accent,
              borderRadius: 3,
              transform: `scaleX(${pct / 100})`,
              transformOrigin: "left",
              transition: "transform 0.6s ease, background 0.3s",
            }} />
          </div>

          {/* Error */}
          {error && (
            <div style={{
              background: `${palette.red}18`, border: `1px solid ${palette.red}`,
              borderRadius: 6, padding: "14px 16px", marginBottom: 20,
              fontFamily: CHART_FONT, fontSize: 12, color: palette.red,
            }}>
              <b>Error:</b> {error}
            </div>
          )}

          {/* Step list */}
          <div style={{
            background: palette.bg1, border: `1px solid ${palette.line}`,
            borderRadius: 8, padding: "8px 20px",
          }}>
            {status ? (
              <StepList status={status} palette={palette} />
            ) : (
              <div style={{
                padding: "32px 0", textAlign: "center",
                fontFamily: CHART_FONT, fontSize: 13, color: palette.ink4,
              }}>
                Starting…
              </div>
            )}
          </div>

          {/* Retry */}
          {isFailed && (
            <div style={{ marginTop: 20 }}>
              <button
                type="button"
                onClick={onRetry}
                style={{
                  background: palette.accent, color: palette.bg,
                  border: "none", borderRadius: 5, padding: "10px 20px",
                  fontFamily: CHART_FONT, fontSize: 13, fontWeight: 600, cursor: "pointer",
                }}
              >
                ← Try Again
              </button>
            </div>
          )}
        </div>

        {/* Right — inventory preview (slides in when available) */}
        {showInv && (
          <InventoryPreview inv={inv!} palette={palette} isComplete={isComplete} />
        )}

      </main>
    </div>
  );
}
