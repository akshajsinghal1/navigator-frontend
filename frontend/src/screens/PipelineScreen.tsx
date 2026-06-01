// ─── Screen 2: Pipeline Progress ─────────────────────────────────────────────
// Polls GET /api/pipeline/{run_id}/status every 2s.
// Shows a clean step-by-step progress view.
// Auto-advances to the dashboard when status === "completed".

import { useEffect, useRef, useState } from "react";
import { useChartTheme } from "../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT } from "../components/charts/chartTheme";
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

// ── Pipeline steps definition ─────────────────────────────────────────────────

const STEPS = [
  { stage: "connecting",           label: "Connecting to Tableau"         },
  { stage: "inventory_extraction", label: "Reading workbook schema"       },
  { stage: "eda",                  label: "Analysing fields & data"       },
  { stage: "orchestrator",         label: "Designing KPIs & personas"     },
  { stage: "domain_agents",        label: "Fetching data for each KPI"    },
  { stage: "chart_agents",         label: "Generating chart specs"        },
  { stage: "assembling",           label: "Assembling Intelligence Config" },
];

const STAGE_ORDER = STEPS.map((s) => s.stage);

function stageIndex(stage: string): number {
  const idx = STAGE_ORDER.indexOf(stage);
  return idx === -1 ? -1 : idx;
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  runId:     string;
  runInfo:   ConnectResult;
  onDone:    (companyId: string) => void;
  onRetry:   () => void;
}

// ── Sub-components ────────────────────────────────────────────────────────────

type Palette = ReturnType<typeof import("../components/charts/chartTheme").getChartPalette>;

function PipelineError({ message, palette }: { message: string; palette: Palette }) {
  return (
    <div style={{
      background: `${palette.red}18`,
      border: `1px solid ${palette.red}`,
      borderRadius: 6, padding: "14px 16px", marginBottom: 20,
      fontFamily: CHART_FONT, fontSize: 12, color: palette.red,
    }}>
      <b>Error:</b> {message}
    </div>
  );
}

function RetryButton({ onClick, palette }: { onClick: () => void; palette: Palette }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: palette.accent, color: palette.bg,
        border: "none", borderRadius: 5, padding: "10px 20px",
        fontFamily: CHART_FONT, fontSize: 13, fontWeight: 600, cursor: "pointer",
      }}
    >
      ← Try Again
    </button>
  );
}

// Spinner via CSS animation injected once
const SPINNER_STYLE = `
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes shimmer {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(100%); }
  }
`;

function StepList({
  status,
  palette,
}: {
  status: PipelineStatus | null;
  palette: Palette;
}) {
  const currentIdx = status
    ? status.status === "completed"
      ? STEPS.length
      : stageIndex(status.stage)
    : -1;

  // Latest message text for the active step (shown as subtitle)
  const latestMsg = status?.messages?.length
    ? status.messages[status.messages.length - 1].message
    : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {STEPS.map((step, i) => {
        const done    = i < currentIdx || status?.status === "completed";
        const active  = i === currentIdx && status?.status === "running";
        const pending = !done && !active;

        const dotColor  = done ? palette.green : active ? palette.accent : palette.line2;
        const labelColor = done ? palette.ink3 : active ? palette.ink : palette.ink4;

        return (
          <div
            key={step.stage}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 14,
              padding: "10px 0",
              borderBottom: i < STEPS.length - 1 ? `1px solid ${palette.line}` : "none",
            }}
          >
            {/* Icon column */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 2 }}>
              {/* Dot / check / spinner */}
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
                  border: `2.5px solid ${palette.accent}`,
                  borderTopColor: "transparent",
                  animation: "spin 0.75s linear infinite",
                  boxSizing: "border-box",
                }} />
              ) : (
                <div style={{
                  width: 18, height: 18, borderRadius: "50%",
                  border: `2px solid ${dotColor}`,
                  boxSizing: "border-box",
                }} />
              )}
            </div>

            {/* Text column */}
            <div style={{ flex: 1, minWidth: 0, paddingTop: 1 }}>
              <div style={{
                fontFamily: CHART_FONT,
                fontSize: 13,
                fontWeight: active ? 600 : 400,
                color: labelColor,
                lineHeight: 1.4,
              }}>
                {step.label}
              </div>

              {/* Show latest log message only for the active step */}
              {active && latestMsg && (
                <div style={{
                  fontFamily: CHART_NUM_FONT,
                  fontSize: 11,
                  color: palette.ink4,
                  marginTop: 3,
                  lineHeight: 1.4,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
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

// ── Component ─────────────────────────────────────────────────────────────────

export function PipelineScreen({ runId, runInfo, onDone, onRetry }: Props) {
  const { palette } = useChartTheme();
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [error, setError]   = useState<string | null>(null);
  const doneRef = useRef(false);

  // Poll every 2s
  // eslint-disable-next-line react-doctor/no-fetch-in-effect
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const res  = await fetch(`/api/pipeline/${runId}/status`);
        if (!res.ok) throw new Error(`Status ${res.status}`);
        const data: PipelineStatus = await res.json();
        setStatus(data);

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

  const pct        = status?.progress_pct ?? 0;
  const isComplete = status?.status === "completed";
  const isFailed   = status?.status === "failed";

  return (
    <div style={{ minHeight: "100vh", background: palette.bg, display: "flex", flexDirection: "column" }}>
      {/* Inject spinner keyframes */}
      <style>{SPINNER_STYLE}</style>

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

      {/* Main */}
      <main style={{ flex: 1, padding: "40px", maxWidth: 560, margin: "0 auto", width: "100%" }}>

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
        {error && <PipelineError message={error} palette={palette} />}

        {/* Step list */}
        <div style={{
          background: palette.bg1,
          border: `1px solid ${palette.line}`,
          borderRadius: 8,
          padding: "8px 20px",
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
            <RetryButton onClick={onRetry} palette={palette} />
          </div>
        )}

      </main>
    </div>
  );
}
