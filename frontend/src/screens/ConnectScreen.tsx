// ─── Screen 1: Connect ────────────────────────────────────────────────────────
// User enters Tableau PAT credentials + workbook URL.
// On submit → POST /api/onboard → transitions to PipelineScreen.

import { useState } from "react";
import { useChartTheme } from "../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT } from "../components/charts/chartTheme";

export interface ConnectResult {
  run_id:     string;
  company_id: string;
  workbook:   string;
}

interface Props {
  onConnect: (result: ConnectResult) => void;
}

interface FormState {
  server_url:   string;
  site_name:    string;
  pat_name:     string;
  pat_secret:   string;
  workbook_url: string;
}

const INITIAL: FormState = {
  server_url:   "https://us-east-1.online.tableau.com",
  site_name:    "",
  pat_name:     "",
  pat_secret:   "",
  workbook_url: "",
};

// ── Field component ───────────────────────────────────────────────────────────

function Field({
  label, value, onChange, placeholder, type = "text", hint, palette,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; type?: string; hint?: string;
  palette: ReturnType<typeof import("../components/charts/chartTheme").getChartPalette>;
}) {
  const [focused, setFocused] = useState(false);
  const inputStyle = {
    background: palette.bg2,
    border: `1px solid ${focused ? palette.accent : palette.line2}`,
    borderRadius: 4,
    padding: "9px 12px",
    fontFamily: CHART_NUM_FONT,
    fontSize: 12,
    color: palette.ink,
    outline: focused ? `2px solid ${palette.accent}` : "none",
    outlineOffset: 2,
    transition: "border-color 0.15s",
    width: "100%",
    boxSizing: "border-box" as const,
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <label style={{
        fontFamily: CHART_NUM_FONT, fontSize: 12, fontWeight: 600,
        color: palette.ink3, letterSpacing: "0.04em", textTransform: "uppercase",
      }}>
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={label}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={inputStyle}
        autoComplete="off"
        spellCheck={false}
      />
      {hint && (
        <span style={{ fontFamily: CHART_FONT, fontSize: 12, color: palette.ink4 }}>
          {hint}
        </span>
      )}
    </div>
  );
}

// ── Shared style constants ────────────────────────────────────────────────────

const submitBtnBase = {
  border: "none",
  borderRadius: 5,
  padding: "12px 24px",
  fontFamily: CHART_FONT,
  fontSize: 13,
  fontWeight: 600,
  transition: "background 0.15s, color 0.15s",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 8,
} as const;

// ── Main component ────────────────────────────────────────────────────────────

export function ConnectScreen({ onConnect }: Props) {
  const { palette } = useChartTheme();
  const [form, setForm] = useState<FormState>(INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (key: keyof FormState) => (val: string) =>
    setForm((f) => ({ ...f, [key]: val }));

  const canSubmit =
    form.server_url && form.site_name && form.pat_name &&
    form.pat_secret && form.workbook_url && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);

    // Auto-generate company_id from workbook URL
    const company_id = form.workbook_url
      .toLowerCase().replace(/[^a-z0-9_-]/g, "_").replace(/_+/g, "_").replace(/^_|_$/g, "")
      || "company";

    try {
      const res = await fetch("/api/onboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tableau_server_url:   form.server_url.trim(),
          tableau_site_name:    form.site_name.trim(),
          tableau_pat_name:     form.pat_name.trim(),
          tableau_pat_secret:   form.pat_secret.trim(),
          workbook_content_url: form.workbook_url.trim(),
          company_id,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }

      const data = await res.json();
      onConnect({
        run_id:     data.run_id,
        company_id: data.company_id,
        workbook:   form.workbook_url.trim(),
      });
    } catch (err) {
      setError(String(err));
      setSubmitting(false);
    }
  }

  return (
    <div style={{
      minHeight: "100vh", background: palette.bg,
      display: "flex", alignItems: "center", justifyContent: "center",
      padding: "40px 20px",
    }}>
      <div style={{ width: "100%", maxWidth: 480 }}>

        {/* Brand */}
        <div style={{ textAlign: "center", marginBottom: 40 }}>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <svg width="28" height="28" viewBox="0 0 22 22" fill="none">
              <circle cx="11" cy="11" r="5" fill={palette.accent} opacity="0.9" />
              <circle cx="11" cy="11" r="9" stroke={palette.accent} strokeWidth="1.5" opacity="0.35" />
              <circle cx="11" cy="11" r="3" fill={palette.bg} />
            </svg>
            <span style={{ fontFamily: CHART_FONT, fontWeight: 700, fontSize: 20, color: palette.ink }}>
              Navigator
            </span>
          </div>
          <p style={{ fontFamily: CHART_FONT, fontSize: 13, color: palette.ink3, margin: 0 }}>
            Connect your Tableau workbook to generate AI-powered intelligence
          </p>
        </div>

        {/* Form card */}
        <form
          onSubmit={handleSubmit}
          style={{
            background: palette.bg1,
            border: `1px solid ${palette.line}`,
            borderRadius: 8,
            padding: "28px 32px",
            display: "flex",
            flexDirection: "column",
            gap: 20,
          }}
        >

          {/* Section: Tableau */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <span style={{
              fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink4,
              letterSpacing: "0.04em", textTransform: "uppercase",
            }}>
              Tableau Connection
            </span>

            <Field
              label="Server URL"
              value={form.server_url}
              onChange={set("server_url")}
              placeholder="https://us-east-1.online.tableau.com"
              palette={palette}
            />

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <Field
                label="Site Name"
                value={form.site_name}
                onChange={set("site_name")}
                placeholder="yoursite"
                hint="The site ID in your Tableau URL"
                palette={palette}
              />
              <Field
                label="PAT Name"
                value={form.pat_name}
                onChange={set("pat_name")}
                placeholder="my-token"
                hint="Personal Access Token name"
                palette={palette}
              />
            </div>

            <Field
              label="PAT Secret"
              value={form.pat_secret}
              onChange={set("pat_secret")}
              type="password"
              placeholder="••••••••••••••••"
              hint="Never stored — used only for this pipeline run"
              palette={palette}
            />
          </div>

          {/* Divider */}
          <div style={{ height: 1, background: palette.line }} />

          {/* Section: Workbook */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <span style={{
              fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink4,
              letterSpacing: "0.04em", textTransform: "uppercase",
            }}>
              Workbook
            </span>
            <Field
              label="Workbook Content URL"
              value={form.workbook_url}
              onChange={set("workbook_url")}
              placeholder="Superstore"
              hint='The content URL shown in your Tableau browser URL — e.g. "Superstore" or "WorldIndicators"'
              palette={palette}
            />
          </div>

          {/* Error */}
          {error && (
            <div style={{
              background: `${palette.red}18`,
              border: `1px solid ${palette.red}`,
              borderRadius: 4, padding: "10px 12px",
              fontFamily: CHART_FONT, fontSize: 12, color: palette.red,
            }}>
              {error}
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={!canSubmit}
            style={{
              ...submitBtnBase,
              background: canSubmit ? palette.accent : palette.bg3,
              color: canSubmit ? palette.bg : palette.ink4,
              cursor: canSubmit ? "pointer" : "not-allowed",
            }}
          >
            {submitting ? (
              <>
                <span style={{
                  width: 14, height: 14,
                  border: `2px solid ${palette.bg}40`,
                  borderTop: `2px solid ${palette.bg}`,
                  borderRadius: "50%",
                  animation: "spin 0.7s linear infinite",
                  display: "inline-block",
                }} />
                Starting pipeline…
              </>
            ) : (
              "Run Pipeline →"
            )}
          </button>
        </form>

        <p style={{
          textAlign: "center", marginTop: 16,
          fontFamily: CHART_NUM_FONT, fontSize: 12, color: palette.ink4,
        }}>
          Pipeline takes 2–4 minutes · Zero data stored in Navigator servers
        </p>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
