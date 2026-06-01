// ─── RadarChart ───────────────────────────────────────────────────────────────
// Compare multivariate performance across dimensions (dept load).
// Data shape: { axes: { name: string, max: number }[], series: { name: string, data: number[] }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface RadarChartData {
  axes: { name: string; max: number }[];
  series: { name: string; data: number[]; color?: string }[];
}

interface RadarChartProps {
  data: RadarChartData;
  height?: number;
}

export function RadarChart({ data, height = 260 }: RadarChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 700,
    animationEasing: "cubicOut",
    color: colors,
    legend: {
      bottom: 0, left: "center",
      itemWidth: 10, itemHeight: 10,
      textStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    },
    tooltip: {
      ...chartTooltip(palette),
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number[]; marker: string };
        const rows = data.axes.map((ax, i) => `${ax.name}: ${p.value[i]}`);
        return [`<b style="font-family:${CHART_FONT}">${p.name}</b>`, ...rows].join("<br/>");
      },
    },
    radar: {
      indicator: data.axes,
      shape: "polygon",
      splitNumber: 4,
      axisName: { color: palette.ink2, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      splitLine: { lineStyle: { color: palette.line2 } },
      splitArea: { areaStyle: { color: ["transparent", translucent(palette.bg3, 0.6)] } },
      axisLine: { lineStyle: { color: palette.line2 } },
    },
    series: [{
      type: "radar",
      data: data.series.map((s, i) => {
        const color = s.color ?? colors[i % colors.length];
        return {
          name: s.name,
          value: s.data,
          lineStyle: { color, width: 1.8 },
          itemStyle: { color },
          areaStyle: { color: translucent(color, 0.18) },
        };
      }),
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
