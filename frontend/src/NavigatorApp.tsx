// ─── NavigatorApp ─────────────────────────────────────────────────────────────
// Universal frontend — works with ANY Navigator Intelligence Config.
//
// Data flow:
//   1. Read ?workbook= from URL (or VITE_WORKBOOK_ID env, or prompt user)
//   2. GET /api/dashboard/{workbook} → Intelligence Config
//   3. Derive persona tabs from config.personas (dynamic, not hardcoded)
//   4. Render NavigatorCanvas for the selected persona
//   5. Each KPI card fetches live data from GET /api/viewdata at render time
//   6. useDataFreshness polls /api/freshness every 5 min and silently
//      re-fetches the config when Tableau data updates
//
// This file has ZERO domain knowledge — no hospital, no superstore.
// It renders whatever the pipeline designed.

import { useEffect, useCallback, useState } from "react";
import { ChartThemeProvider } from "./context/ChartThemeContext";
import { NavigatorCanvas } from "./components/NavigatorCanvas";
import { useChartTheme } from "./context/ChartThemeContext";
import { api } from "./api/client";
import { CHART_FONT, CHART_NUM_FONT } from "./components/charts/chartTheme";
import { useDataFreshness } from "./hooks/useDataFreshness";
import type { NavigatorConfig, NavigatorPersona } from "./types/navigator";

// ── Workbook resolver ─────────────────────────────────────────────────────────
// Priority: ?workbook= URL param → VITE env var → "Superstore" default

function resolveWorkbook(): string {
  const params = new URLSearchParams(window.location.search);
  return (
    params.get("workbook") ||
    (import.meta as { env?: Record<string, string> }).env?.VITE_WORKBOOK_ID ||
    "Superstore"
  );
}

/** Convert a workbook content URL to a company_id (mirrors backend _slugify). */
function toCompanyId(workbookId: string): string {
  return workbookId
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "_")
    .replace(/^_+|_+$/g, "") || "company";
}

// ── Inner app (inside ChartThemeProvider) ─────────────────────────────────────

interface NavigatorInnerProps {
  workbookId?: string;   // override URL param (used by DemoApp)
  onBack?: () => void;   // "← New workbook" callback (used by DemoApp)
}

export function NavigatorInner({ workbookId: propWorkbookId, onBack }: NavigatorInnerProps = {}) {
  const { palette } = useChartTheme();
  const workbookId = propWorkbookId ?? resolveWorkbook();
  const companyId  = toCompanyId(workbookId);

  const [config, setConfig]     = useState<NavigatorConfig | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [personaIdx, setPersonaIdx] = useState(0);
  // Derive loading: true only while neither config nor error is available yet
  const loading = config === null && error === null;

  // ── Theme state ────────────────────────────────────────────────────────────
  const [isLight, setIsLight] = useState<boolean>(() => {
    const stored = localStorage.getItem("nav-theme");
    return stored !== null ? stored === "light" : true;
  });

  // Apply theme on mount and whenever isLight changes
  useEffect(() => {
    if (isLight) {
      document.documentElement.setAttribute("data-theme", "light");
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    localStorage.setItem("nav-theme", isLight ? "light" : "dark");
  }, [isLight]);

  // ── Freshness polling ──────────────────────────────────────────────────────
  const { lastRefreshedAt, status: freshnessStatus, dataUpdated, clearDataUpdated } =
    useDataFreshness(companyId);

  // ── Config loader ──────────────────────────────────────────────────────────
  const loadConfig = useCallback(() => {
    // Reset to loading state by clearing config and error
    setConfig(null);
    setError(null);
    api.intelligenceConfig(workbookId)
      .then((cfg) => {
        setConfig(cfg);
        setPersonaIdx(0);
      })
      .catch((err) => setError(String(err)));
  }, [workbookId]);

  // Initial load — config is async-fetched, not synchronously derivable
  // eslint-disable-next-line react-doctor/no-derived-state
  useEffect(() => { loadConfig(); }, [loadConfig]);

  // Silent re-fetch when Tableau data updates.
  // clearDataUpdated() is intentionally called inside this effect to reset the
  // freshness flag after we've acknowledged it — this is not a data-to-parent
  // anti-pattern but a deliberate data-refresh acknowledgement flow.
  useEffect(() => {
    if (!dataUpdated) return;
    clearDataUpdated(); // eslint-disable-line react-doctor/no-pass-data-to-parent
    // Re-fetch without showing the full-screen spinner (no config=null reset)
    api.intelligenceConfig(workbookId)
      .then((cfg) => {
        setConfig(cfg);
        // Keep personaIdx where the user is — don't reset to 0
      })
      .catch(() => { /* silently ignore — user keeps stale view */ });
  }, [dataUpdated, workbookId, clearDataUpdated]);

  const activePersona: NavigatorPersona | null = config?.personas?.[personaIdx] ?? null;

  // ── Loading ────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        background: palette.bg, flexDirection: "column", gap: 12,
      }}>
        <div style={{ width: 36, height: 36, border: `2px solid ${palette.line2}`,
          borderTop: `2px solid ${palette.accent}`, borderRadius: "50%",
          animation: "spin 0.8s linear infinite",
        }} />
        <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink3 }}>
          loading intelligence config…
        </span>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  // ── Error ──────────────────────────────────────────────────────────────────
  if (error || !config) {
    const errPgStyle = {
      minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      background: palette.bg, flexDirection: "column" as const, gap: 16, padding: 40,
    };
    return (
      <div style={errPgStyle}>
        <span style={{ fontSize: 32 }}>⚠</span>
        <span style={{ fontFamily: CHART_FONT, fontSize: 14, color: palette.red, textAlign: "center" }}>
          {error ?? "Intelligence Config not found"}
        </span>
        <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink3, textAlign: "center" }}>
          Run the pipeline first:{" "}
          <code style={{ background: palette.bg2, padding: "2px 6px", borderRadius: 3 }}>
            python run_pipeline.py --workbook {workbookId}
          </code>
        </span>
        <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink4, textAlign: "center" }}>
          Then restart the API:{" "}
          <code style={{ background: palette.bg2, padding: "2px 6px", borderRadius: 3 }}>
            uvicorn api.main:app --reload --port 8000
          </code>
        </span>
      </div>
    );
  }

  // ── Freshness indicator ────────────────────────────────────────────────────
  const freshnessLabel = (() => {
    if (freshnessStatus === "refreshing") return "refreshing…";
    if (lastRefreshedAt) {
      try {
        return `data refreshed ${new Date(lastRefreshedAt).toLocaleString()}`;
      } catch {
        return `data refreshed ${lastRefreshedAt}`;
      }
    }
    return null;
  })();

  // ── Computed styles (palette-dependent, extracted from JSX) ──────────────────
  const headerStyle = {
    padding: "14px 40px",
    borderBottom: `1px solid ${palette.line}`,
    display: "flex", alignItems: "center", gap: 20,
    background: palette.bg,
    position: "sticky" as const, top: 0, zIndex: 10,
  };
  const errorPageStyle = {
    minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
    background: palette.bg, flexDirection: "column" as const, gap: 16, padding: 40,
  };
  const tabBtnBase = {
    padding: "12px 20px",
    fontFamily: CHART_FONT,
    fontSize: 12,
    background: "none",
    border: "none" as const,
    cursor: "pointer" as const,
    transition: "color 0.15s",
    whiteSpace: "nowrap" as const,
  };
  const backBtnStyle = {
    background: "none", border: `1px solid ${palette.line2}`, borderRadius: 4,
    padding: "5px 10px", fontFamily: CHART_FONT, fontSize: 12,
    color: palette.ink3, cursor: "pointer" as const, flexShrink: 0,
  };
  const themeBtnStyle = {
    display: "flex", alignItems: "center", justifyContent: "center",
    height: 28, minWidth: 42,
    padding: "0 10px",
    background: palette.bg2,
    border: `1px solid ${palette.line2}`,
    borderRadius: 14,
    color: palette.ink3,
    fontSize: 14,
    lineHeight: 1,
    cursor: "pointer" as const,
    flexShrink: 0,
    transition: "background 0.15s, color 0.15s",
  };

  // ── Main UI ────────────────────────────────────────────────────────────────
  return (
    <div style={{ minHeight: "100vh", background: palette.bg, display: "flex", flexDirection: "column" }}>

      {/* ── Header ── */}
      <header style={headerStyle}>
        {/* Brand */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
            <circle cx="11" cy="11" r="5" fill={palette.accent} opacity="0.9" />
            <circle cx="11" cy="11" r="9" stroke={palette.accent} strokeWidth="1.5" opacity="0.35" />
            <circle cx="11" cy="11" r="3" fill={palette.bg} />
          </svg>
          <span style={{
            fontFamily: CHART_FONT, fontWeight: 700, fontSize: 14,
            color: palette.ink, letterSpacing: "-0.01em",
          }}>
            Navigator
          </span>
          <span style={{ width: 1, height: 16, background: palette.line2 }} />
          <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink3 }}>
            {config.workbook.name}
          </span>
        </div>

        {/* Objective pill */}
        <div style={{
          flex: 1,
          fontFamily: CHART_FONT, fontSize: 12, color: palette.ink3,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>
          {config.objective}
        </div>

        {/* Back button (when embedded in DemoApp) */}
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            style={backBtnStyle}
          >
            ← New workbook
          </button>
        )}

        {/* Freshness status */}
        {freshnessLabel && (
          <div style={{
            display: "flex", alignItems: "center", gap: 5, flexShrink: 0,
          }}>
            {freshnessStatus === "refreshing" ? (
              <div style={{
                width: 6, height: 6, borderRadius: "50%",
                border: `1px solid ${palette.accent}`,
                borderTop: `1px solid transparent`,
                animation: "spin 0.8s linear infinite",
              }} />
            ) : (
              <div style={{
                width: 6, height: 6, borderRadius: "50%",
                background: palette.green ?? "#22c55e",
              }} />
            )}
            <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink4 }}>
              {freshnessLabel}
            </span>
          </div>
        )}

        {/* Theme toggle */}
        <button
          type="button"
          onClick={() => setIsLight((prev) => !prev)}
          style={themeBtnStyle}
          title={isLight ? "Switch to dark mode" : "Switch to light mode"}
          aria-label={isLight ? "Switch to dark mode" : "Switch to light mode"}
        >
          {isLight ? "☀" : "☾"}
        </button>

        {/* Generated-at */}
        <span style={{ fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink4, flexShrink: 0 }}>
          {config.refreshed_at
            ? `refreshed ${new Date(config.refreshed_at).toLocaleString()}`
            : `generated ${new Date(config.generated_at).toLocaleString()}`}
        </span>
      </header>

      {/* ── Persona tabs ── */}
      <div style={{
        padding: "0 40px",
        borderBottom: `1px solid ${palette.line}`,
        display: "flex", gap: 0,
        background: palette.bg1,
      }}>
        {config.personas.map((pv, i) => {
          const active = i === personaIdx;
          return (
            <button
              key={pv.persona.role}
              type="button"
              onClick={() => setPersonaIdx(i)}
              style={{
                ...tabBtnBase,
                fontWeight: active ? 600 : 400,
                color: active ? palette.accent : palette.ink3,
                borderBottom: active ? `2px solid ${palette.accent}` : "2px solid transparent",
              }}
            >
              {pv.persona.role}
            </button>
          );
        })}
      </div>

      {/* Focus areas removed — they're visible in the persona tab subtitle */}

      {/* ── Canvas ── */}
      <main style={{ flex: 1, padding: "16px 40px 32px" }}>
        {activePersona && (
          <NavigatorCanvas
            key={activePersona.persona.role}
            persona={activePersona}
            workbookId={workbookId}
          />
        )}
      </main>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

// ── Export ────────────────────────────────────────────────────────────────────

export default function NavigatorApp() {
  return (
    <ChartThemeProvider>
      <NavigatorInner />
    </ChartThemeProvider>
  );
}
