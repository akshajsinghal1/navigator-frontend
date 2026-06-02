// ─── DemoApp ──────────────────────────────────────────────────────────────────
// 4-screen internal demo:
//
//   Screen 1 — ConnectScreen   : enter PAT + workbook details
//   Screen 2 — PipelineScreen  : live agent progress
//   Screen 3 — InventoryScreen : what Navigator read from Tableau
//   Screen 4 — NavigatorApp    : fully rendered intelligence dashboard
//
// Navigation is state-based (no router needed).
// URL deeplink: ?workbook=X&company=Y skips to Screen 4 directly.

import { useRef, useState } from "react";
import { ChartThemeProvider } from "./context/ChartThemeContext";
import { ConnectScreen }    from "./screens/ConnectScreen";
import { PipelineScreen }   from "./screens/PipelineScreen";
import { InventoryScreen }  from "./screens/InventoryScreen";
import { NavigatorInner }   from "./NavigatorApp";
import type { ConnectResult } from "./screens/ConnectScreen";

type Screen = "connect" | "pipeline" | "inventory" | "dashboard";

function resolveInitialScreen(): { screen: Screen; workbook?: string; companyId?: string } {
  const params    = new URLSearchParams(window.location.search);
  const workbook  = params.get("workbook") ?? undefined;
  const companyId = params.get("company")  ?? undefined;

  // If both workbook and company are in the URL, go straight to dashboard
  if (workbook && companyId) return { screen: "dashboard", workbook, companyId };
  return { screen: "connect" };
}

// ── Inner (inside ChartThemeProvider) ────────────────────────────────────────

function DemoInner() {
  const init = resolveInitialScreen();
  const [screen, setScreen]       = useState<Screen>(init.screen);
  const [runInfo, setRunInfo]     = useState<ConnectResult | null>(null);
  const [companyId, setCompanyId] = useState<string>(init.companyId ?? "");
  const workbookIdRef             = useRef<string>(init.workbook ?? "");

  // Keep URL in sync
  function syncUrl(nextScreen: Screen, nextCompanyId: string) {
    const params = new URLSearchParams(window.location.search);
    if (nextScreen === "dashboard" && nextCompanyId) {
      params.set("company", nextCompanyId);
      if (workbookIdRef.current) params.set("workbook", workbookIdRef.current);
    } else {
      params.delete("company");
      params.delete("workbook");
    }
    const qs = params.toString();
    window.history.replaceState({}, "", qs ? `?${qs}` : window.location.pathname);
  }

  // ── Screen 1 → Screen 2 ─────────────────────────────────────────────────
  function handleConnect(result: ConnectResult) {
    workbookIdRef.current = result.workbook;
    setRunInfo(result);
    setScreen("pipeline");
    syncUrl("pipeline", "");
  }

  // ── Screen 2 → Screen 3 ─────────────────────────────────────────────────
  function handlePipelineDone(company: string) {
    setCompanyId(company);
    setScreen("inventory");
    syncUrl("inventory", "");
  }

  // ── Screen 3 → Screen 4 ─────────────────────────────────────────────────
  function handleInventoryContinue() {
    setScreen("dashboard");
    syncUrl("dashboard", companyId);
  }

  // ── Screen 4 back → Screen 1 ────────────────────────────────────────────
  function handleReset() {
    workbookIdRef.current = "";
    setScreen("connect");
    setRunInfo(null);
    setCompanyId("");
    syncUrl("connect", "");
  }

  // ── Render ────────────────────────────────────────────────────────────────
  if (screen === "connect") {
    return <ConnectScreen onConnect={handleConnect} />;
  }

  if (screen === "pipeline" && runInfo) {
    return (
      <PipelineScreen
        runId={runInfo.run_id}
        runInfo={runInfo}
        onDone={handlePipelineDone}
        onRetry={handleReset}
      />
    );
  }

  if (screen === "inventory" && companyId) {
    return (
      <InventoryScreen
        companyId={companyId}
        workbook={workbookIdRef.current}
        onContinue={handleInventoryContinue}
      />
    );
  }

  if (screen === "dashboard") {
    return (
      <NavigatorInner
        workbookId={companyId}
        onBack={handleReset}
      />
    );
  }

  return null;
}

// ── Export ────────────────────────────────────────────────────────────────────

export default function DemoApp() {
  return (
    <ChartThemeProvider>
      <DemoInner />
    </ChartThemeProvider>
  );
}
