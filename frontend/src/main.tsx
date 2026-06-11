import React from "react";
import ReactDOM from "react-dom/client";
import DemoApp from "./DemoApp";
import "./styles/globals.css";

// DemoApp — 3-screen internal demo:
//   Screen 1: Connect (PAT + workbook form)
//   Screen 2: Pipeline progress (live agent log)
//   Screen 3: Navigator dashboard (any workbook)
//
// Deeplink to dashboard directly:
//   http://localhost:5173/?workbook=Superstore
//   http://localhost:5173/?workbook=NAVIGATOR_DEMO   (pinned demo config)

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DemoApp />
  </React.StrictMode>
);
