// ─── DemoApp ──────────────────────────────────────────────────────────────────
// 3-screen internal demo:
//
//   Screen 1 — ConnectScreen  : enter PAT + workbook details
//   Screen 2 — PipelineScreen : live agent progress + inventory preview
//   Screen 3 — NavigatorApp   : fully rendered intelligence dashboard
//
// Navigation is state-based (no router needed for 3 screens).
// URL deeplink: ?workbook=X&company=Y skips to Screen 3 directly.

import { useRef, useState } from "react";
import { ChartThemeProvider } from "./context/ChartThemeContext";
import { ConnectScreen }   from "./screens/ConnectScreen";
import { PipelineScreen }  from "./screens/PipelineScreen";
import { NavigatorInner }  from "./NavigatorApp";
import type { ConnectResult } from "./screens/ConnectScreen";

type Screen = "connect" | "pipeline" | "dashboard";

function resolveInitialScreen(): { screen: Screen; workbook?: string; companyId?: string } {
  const params = new URLSearchParams(window.location.search);
  const workbook   = params.get("workbook") ?? undefined;
  const companyId  = params.get("company")  ?? undefined;

  if (workbook && companyId) return { screen: "dashboard", workbook, companyId };
  return { screen: "connect" };
}

function DemoInner() {
  const init = resolveInitialScreen();
  const [screen, setScreen]       = useState<Screen>(init.screen);
  const [runInfo, setRunInfo]     = useState<ConnectResult | null>(null);
  const [companyId, setCompanyId] = useState<string>(init.companyId ?? "");
  const workbookIdRef             = useRef<string>(init.workbook ?? "");

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

  function handleConnect(result: ConnectResult) {
    workbookIdRef.current = result.workbook;
    setRunInfo(result);
    setScreen("pipeline");
    syncUrl("pipeline", "");
  }

  function handleDone(company: string) {
    setCompanyId(company);
    setScreen("dashboard");
    syncUrl("dashboard", company);
  }

  function handleReset() {
    workbookIdRef.current = "";
    setScreen("connect");
    setRunInfo(null);
    setCompanyId("");
    syncUrl("connect", "");
  }

  if (screen === "connect") {
    return <ConnectScreen onConnect={handleConnect} />;
  }

  if (screen === "pipeline" && runInfo) {
    return (
      <PipelineScreen
        runId={runInfo.run_id}
        runInfo={runInfo}
        onDone={handleDone}
        onRetry={handleReset}
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

export default function DemoApp() {
  return (
    <ChartThemeProvider>
      <DemoInner />
    </ChartThemeProvider>
  );
}
