// ─── BoxPlotChart ─────────────────────────────────────────────────────────────
// Statistical distribution — LOS variance across facilities or DRGs.
// Data shape: { labels: string[], data: [min, Q1, median, Q3, max][] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface BoxPlotChartData {
  labels: string[];
  // ECharts boxplot format: [min, Q1, median, Q3, max]
  data: [number, number, number, number, number][];
}

interface BoxPlotChartProps {
  data: BoxPlotChartData;
  height?: number;
}

export function BoxPlotChart({ data, height = 260 }: BoxPlotChartProps) {
  const { palette } = useChartTheme();

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 650,
    animationEasing: "cubicOut",
    grid: { top: 18, bottom: 40, left: 46, right: 18 },
    tooltip: {
      ...chartTooltip(palette),
      trigger: "axis",
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const p = list[0] as { name: string; value: [number, number, number, number, number] };
        const [min, q1, med, q3, max] = p.value;
        return [
          `<b style="font-family:${CHART_FONT}">${p.name}</b>`,
          `Min: ${min}`, `Q1: ${q1}`, `Median: ${med}`, `Q3: ${q3}`, `Max: ${max}`,
        ].join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: data.labels,
      axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.line2 } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: palette.line, type: "dashed" } },
    },
    series: [{
      name: "Distribution",
      type: "boxplot",
      data: data.data,
      itemStyle: {
        color: translucent(palette.accent, 0.4),
        borderColor: palette.accent,
        borderWidth: 1.5,
      },
      emphasis: {
        itemStyle: {
          color: translucent(palette.accent, 0.6),
          borderColor: palette.accent,
          shadowBlur: 14,
          shadowColor: "rgba(0,0,0,.3)",
        },
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
