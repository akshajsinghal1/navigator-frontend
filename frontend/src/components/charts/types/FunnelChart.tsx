// ─── FunnelChart ──────────────────────────────────────────────────────────────
// Sequential process stages — sales pipeline, revenue exposure tiers.
// Data shape: { items: { name: string, value: number }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip } from "../chartTheme";

export interface FunnelChartData {
  items: { name: string; value: number }[];
}

interface FunnelChartProps {
  data: FunnelChartData;
  height?: number;
}

export function FunnelChart({ data, height = 260 }: FunnelChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.red, palette.amber, palette.accent, palette.green, palette.ink2];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 700,
    animationEasing: "cubicOut",
    color: colors,
    tooltip: {
      ...chartTooltip(palette),
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number; percent: number; marker: string };
        return `${p.marker} <b style="font-family:${CHART_FONT}">${p.name}</b><br/>${p.value} (${p.percent}%)`;
      },
    },
    series: [{
      type: "funnel",
      left: "10%",
      width: "80%",
      top: 20,
      bottom: 20,
      data: data.items,
      sort: "none",
      gap: 3,
      label: {
        position: "inside",
        color: palette.bg,
        fontFamily: CHART_FONT,
        fontSize: 11,
      },
      itemStyle: { borderColor: "transparent", borderWidth: 0 },
      emphasis: {
        label: { fontSize: 13, fontWeight: "bold" },
      },
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
