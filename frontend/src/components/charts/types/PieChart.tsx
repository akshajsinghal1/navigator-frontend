// ─── PieChart ────────────────────────────────────────────────────────────────
// Best for: proportional relationships (payer mix, status distribution).
// Data shape: { items: { name: string, value: number }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip } from "../chartTheme";

export interface PieChartData {
  items: { name: string; value: number }[];
}

interface PieChartProps {
  data: PieChartData;
  height?: number;
}

export function PieChart({ data, height = 260 }: PieChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2, palette.ink3];

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
    legend: {
      orient: "vertical",
      right: 0,
      top: "center",
      itemWidth: 10, itemHeight: 10,
      textStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    },
    series: [{
      type: "pie",
      radius: ["0%", "68%"],
      center: ["40%", "50%"],
      data: data.items,
      label: { show: false },
      emphasis: {
        itemStyle: { shadowBlur: 18, shadowColor: "rgba(0,0,0,.4)" },
        label: { show: true, color: palette.ink, fontFamily: CHART_FONT, fontSize: 12 },
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
