// ─── DoughnutChart ────────────────────────────────────────────────────────────
// Pie chart with a center hole — ideal for showing a KPI total in the middle.
// Data shape: { items: { name: string, value: number }[], centerLabel?: string }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip } from "../chartTheme";

export interface DoughnutChartData {
  items: { name: string; value: number }[];
  centerLabel?: string;
}

interface DoughnutChartProps {
  data: DoughnutChartData;
  height?: number;
}

export function DoughnutChart({ data, height = 260 }: DoughnutChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2, palette.ink3];
  const total = data.items.reduce((sum, d) => sum + d.value, 0);

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
    graphic: [{
      type: "group",
      left: "center",
      top: "center",
      children: [
        {
          type: "text",
          style: {
            text: data.centerLabel ?? String(total),
            fill: palette.ink,
            font: `bold 22px ${CHART_NUM_FONT}`,
          },
          left: "center",
          top: -12,
        },
        {
          type: "text",
          style: {
            text: "total",
            fill: palette.ink3,
            font: `10px ${CHART_NUM_FONT}`,
          },
          left: "center",
          top: 14,
        },
      ],
    }],
    series: [{
      type: "pie",
      radius: ["44%", "68%"],
      center: ["40%", "50%"],
      data: data.items,
      label: { show: false },
      emphasis: {
        itemStyle: { shadowBlur: 18, shadowColor: "rgba(0,0,0,.4)" },
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
