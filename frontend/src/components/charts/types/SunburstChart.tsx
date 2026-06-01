// ─── SunburstChart ────────────────────────────────────────────────────────────
// Radial hierarchical visualization for drill-down structures.
// Data shape: { data: { name: string, value?: number, children?: ... }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip } from "../chartTheme";

export interface SunburstNode {
  name: string;
  value?: number;
  children?: SunburstNode[];
}

export interface SunburstChartData {
  data: SunburstNode[];
}

interface SunburstChartProps {
  data: SunburstChartData;
  height?: number;
}

export function SunburstChart({ data, height = 260 }: SunburstChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 700,
    animationEasing: "cubicOut",
    color: colors,
    tooltip: {
      ...chartTooltip(palette),
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number };
        return `<b style="font-family:${CHART_FONT}">${p.name}</b><br/>Value: ${p.value ?? "–"}`;
      },
    },
    series: [{
      type: "sunburst",
      data: data.data,
      radius: ["20%", "90%"],
      sort: undefined,
      label: {
        rotate: "radial",
        color: palette.bg,
        fontFamily: CHART_NUM_FONT,
        fontSize: 10,
        overflow: "truncate",
      },
      itemStyle: {
        borderColor: palette.bg1,
        borderWidth: 2,
      },
      levels: [
        {},
        { r0: "20%", r: "50%", label: { align: "right" } },
        { r0: "50%", r: "70%", label: { position: "outside", padding: 3 } },
        { r0: "70%", r: "90%", label: { position: "outside" } },
      ],
    }],
  };

  return (
    <ReactECharts
      option={option}
      style={{ height, width: "100%" }}
      notMerge
    />
  );
}
