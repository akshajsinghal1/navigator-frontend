// ─── MapChart (Stub) ──────────────────────────────────────────────────────────
// Geographic analytics — requires external GeoJSON data.
// For Navigator POC, this renders a placeholder until a geoDataUrl is provided.
// Data shape: { geoDataUrl: string, data: { name: string, value: number }[] }

import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT } from "../chartTheme";

export interface MapChartData {
  geoDataUrl?: string;
  data?: { name: string; value: number }[];
}

interface MapChartProps {
  data: MapChartData;
  height?: number;
}

export function MapChart({ data, height = 260 }: MapChartProps) {
  const { palette } = useChartTheme();

  // Stub: Geographic map requires loading GeoJSON dynamically.
  // Register a geoDataUrl in the slot config to enable full rendering.
  const stubStyle = {
    height, width: "100%",
    display: "flex", flexDirection: "column" as const,
    alignItems: "center", justifyContent: "center",
    border: `1px dashed ${palette.line2}`, borderRadius: 4,
    gap: 8, fontFamily: CHART_FONT,
    color: palette.ink3, fontSize: 12,
    textAlign: "center" as const, padding: 16,
  };

  if (!data.geoDataUrl) {
    return (
      <div style={stubStyle}>
        <span style={{ fontSize: 28 }}>🗺</span>
        <span>Map chart requires a <code>geoDataUrl</code></span>
        <span style={{ fontSize: 12, color: palette.ink4 }}>
          Add <code>geoDataUrl</code> to the slot's <code>optionOverrides</code> in dashboardConfig.json
        </span>
      </div>
    );
  }

  const loadingStyle = {
    height, width: "100%",
    display: "flex", alignItems: "center", justifyContent: "center",
    color: palette.ink3, fontFamily: CHART_FONT, fontSize: 12,
  };

  // Full implementation would dynamically import ECharts Maps here
  // and register the GeoJSON fetched from geoDataUrl.
  return (
    <div style={loadingStyle}>
      Map loading from {data.geoDataUrl}…
    </div>
  );
}
