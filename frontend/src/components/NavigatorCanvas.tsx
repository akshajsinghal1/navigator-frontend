// ─── NavigatorCanvas ─────────────────────────────────────────────────────────
// Config-driven canvas that renders any Navigator Intelligence Config persona.
//
// Layout (per persona):
//   ┌──────────────────────────────────────────────────────────┐
//   │  [Summary card 1]  [Summary card 2]  [Summary card 3]   │  ← AI-written
//   ├──────────────────────────────────────────────────────────┤
//   │  Now · 7D · 30D                            (period bar) │
//   │  Section title                                           │
//   │  [ KPI ]  [ KPI ]  [ KPI ]                              │
//   └──────────────────────────────────────────────────────────┘
//
// Zero hardcoded domain knowledge — everything comes from the config.

import { useState } from "react";
import { useChartTheme } from "../context/ChartThemeContext";
import { NavigatorKpiCard } from "./NavigatorKpiCard";
import { CHART_FONT, CHART_NUM_FONT } from "./charts/chartTheme";
import type { NavigatorPersona, NavigatorSummaryCard } from "../types/navigator";

// ── Period type ───────────────────────────────────────────────────────────────

export type Period = "now" | "7d" | "30d";

const PERIOD_OPTIONS: { key: Period; label: string }[] = [
  { key: "now", label: "Now"  },
  { key: "7d",  label: "7D"   },
  { key: "30d", label: "30D"  },
];

// ── Grid layout helper ────────────────────────────────────────────────────────

function colsForCount(count: number): number {
  if (count === 1) return 12;
  if (count === 2) return 6;
  if (count === 3) return 4;
  return 4; // 3-per-row for 4+
}

// ── Period bar ────────────────────────────────────────────────────────────────

type Palette = ReturnType<typeof import("./charts/chartTheme").getChartPalette>;

function PeriodBar({
  period,
  onChange,
  palette,
}: {
  period:   Period;
  onChange: (p: Period) => void;
  palette:  Palette;
}) {
  return (
    <div style={{
      display: "inline-flex",
      background: palette.bg2,
      border: `1px solid ${palette.line}`,
      borderRadius: 6,
      padding: 2,
      gap: 2,
    }}>
      {PERIOD_OPTIONS.map(({ key, label }) => {
        const active = period === key;
        return (
          <button
            key={key}
            type="button"
            onClick={() => onChange(key)}
            style={{
              fontFamily:  CHART_NUM_FONT,
              fontSize:    12,
              fontWeight:  active ? 700 : 500,
              padding:     "4px 12px",
              borderRadius: 4,
              border:      "none",
              cursor:      "pointer",
              background:  active ? palette.bg1 : "transparent",
              color:       active ? palette.ink  : palette.ink4,
              boxShadow:   active ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
              transition:  "all 0.15s",
              letterSpacing: "0.02em",
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

// ── Summary card ──────────────────────────────────────────────────────────────

const summaryCardBase = {
  flex: 1,
  minWidth: 0,
  borderRadius: 6,
  padding: "14px 16px",
  display: "flex",
  flexDirection: "column" as const,
  gap: 8,
  animation: "summaryIn 0.32s ease both",
  boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
} as const;

function SummaryCardItem({ card }: { card: NavigatorSummaryCard }) {
  const { palette } = useChartTheme();

  const accentColor =
    card.signal === "positive" ? palette.green :
    card.signal === "warning"  ? palette.red   :
    palette.accent;

  const signalLabel =
    card.signal === "positive" ? "● Positive" :
    card.signal === "warning"  ? "▲ Watch"    :
    "◆ Insight";

  return (
    <div style={{
      ...summaryCardBase,
      background: `${accentColor}12`,
      border: `1px solid ${accentColor}33`,
      borderLeft: `3px solid ${accentColor}`,
    }}>
      <span style={{
        fontFamily: CHART_NUM_FONT,
        fontSize: 12,
        fontWeight: 700,
        color: accentColor,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}>
        {signalLabel}
      </span>
      <span style={{
        fontFamily: CHART_FONT,
        fontSize: 13,
        fontWeight: 700,
        color: palette.ink,
        lineHeight: 1.3,
      }}>
        {card.title}
      </span>
      <p style={{
        fontFamily: CHART_FONT,
        fontSize: 12,
        color: palette.ink2,
        lineHeight: 1.6,
        margin: 0,
      }}>
        {card.body}
      </p>
    </div>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  persona:    NavigatorPersona;
  workbookId: string;
}

export function NavigatorCanvas({ persona, workbookId }: Props) {
  const { palette }  = useChartTheme();
  const [period, setPeriod] = useState<Period>("now");
  const hasSummary   = (persona.summary_cards?.length ?? 0) > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32, padding: "8px 0" }}>

      {/* ── AI Summary strip ── */}
      {hasSummary && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span style={{
            fontFamily: CHART_NUM_FONT,
            fontSize: 12,
            fontWeight: 700,
            color: palette.ink4,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}>
            AI Summary
          </span>
          <div style={{ display: "flex", gap: 12 }}>
            {persona.summary_cards!.map((card, i) => (
              <SummaryCardItem key={i + card.title} card={card} />
            ))}
          </div>
        </div>
      )}

      {/* ── Period bar ── */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}>
        <span style={{
          fontFamily: CHART_NUM_FONT,
          fontSize: 11,
          color: palette.ink4,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
        }}>
          {period === "now" ? "Current values" : `Last ${period.toUpperCase()} · live from Tableau`}
        </span>
        <PeriodBar period={period} onChange={setPeriod} palette={palette} />
      </div>

      {/* ── KPI sections ── */}
      {persona.dashboard_sections.map((section) => (
        <section key={section.id}>

          {/* Section header */}
          <div style={{
            display: "flex", alignItems: "baseline", gap: 12,
            marginBottom: 14,
            borderBottom: `1px solid ${palette.line}`,
            paddingBottom: 10,
          }}>
            <span style={{
              fontFamily: CHART_FONT,
              fontSize: 12,
              fontWeight: 700,
              color: palette.ink2,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}>
              {section.title.replace(/_/g, " ")}
            </span>
            {section.description && (
              <span style={{
                fontFamily: CHART_FONT,
                fontSize: 12,
                color: palette.ink4,
              }}>
                {section.description}
              </span>
            )}
            <span style={{
              fontFamily: CHART_NUM_FONT,
              fontSize: 12,
              color: palette.ink4,
              marginLeft: "auto",
            }}>
              {section.kpis.length} KPI{section.kpis.length !== 1 ? "s" : ""}
            </span>
          </div>

          {/* KPI grid */}
          <div style={{
            display: "grid",
            gridTemplateColumns: `repeat(12, 1fr)`,
            gap: 12,
          }}>
            {section.kpis.map((kpi, index) => {
              const colSpan = colsForCount(section.kpis.length);
              const ctype = (kpi.chart?.type ?? "kpi_card").toLowerCase();
              const isCardOnly = ctype === "kpi_card" || ctype === "scorecard";
              const chartHeight = isCardOnly ? 0 : ctype === "map_chart" ? 320 : 200;
              return (
                <div
                  key={kpi.id}
                  style={{
                    gridColumn: `span ${colSpan}`,
                    animation: "kpiEnter 0.28s ease both",
                    animationDelay: `${index * 0.04}s`,
                  }}
                >
                  <NavigatorKpiCard
                    kpi={kpi}
                    workbookId={workbookId}
                    chartHeight={chartHeight}
                    period={period}
                  />
                </div>
              );
            })}
          </div>
        </section>
      ))}

      <style>{`
        @keyframes kpiEnter {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes summaryIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
