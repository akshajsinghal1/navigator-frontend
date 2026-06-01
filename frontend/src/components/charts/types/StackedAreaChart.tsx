// ─── StackedAreaChart ─────────────────────────────────────────────────────────
// Cumulative totals across multiple series over time.
// Data shape: { labels: string[], series: { name: string, data: number[] }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface StackedAreaChartData {
  labels: string[];
  series: { name: string; data: number[] }[];
}

interface StackedAreaChartProps {
  data: StackedAreaChartData;
  height?: number;
}

export function StackedAreaChart({ data, height = 260 }: StackedAreaChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 700,
    animationEasing: "cubicOut",
    color: colors,
    grid: { top: 36, bottom: 32, left: 46, right: 18 },
    legend: {
      top: 0, right: 0,
      itemWidth: 13, itemHeight: 2,
      textStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    },
    tooltip: { ...chartTooltip(palette), trigger: "axis" },
    xAxis: {
      type: "category",
      data: data.labels,
      boundaryGap: false,
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
    series: data.series.map((s, i) => {
      const color = colors[i % colors.length];
      return {
        name: s.name,
        type: "line",
        stack: "total",
        data: s.data,
        smooth: false,
        symbol: "none",
        lineStyle: { color, width: 1 },
        itemStyle: { color },
        areaStyle: { color: translucent(color, 0.5) },
      };
    }),
  };

  return (
    <ReactECharts
      option={option}
      style={{ height, width: "100%" }}
      notMerge
    />
  );
}
