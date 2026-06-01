// ─── LineChart ───────────────────────────────────────────────────────────────
// Best for: trends over time, continuous metrics, census history.
// Data shape: { labels: string[], series: { name: string, data: number[] }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface LineChartData {
  labels: string[];
  series: { name: string; data: number[]; unit?: string }[];
}

interface LineChartProps {
  data: LineChartData;
  height?: number;
}

export function LineChart({ data, height = 260 }: LineChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.ink, palette.green, palette.amber, palette.red];

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
    series: data.series.map((s, i) => ({
      name: s.name,
      type: "line",
      data: s.data,
      smooth: false,
      symbol: "none",
      lineStyle: { color: colors[i % colors.length], width: 1.8 },
      itemStyle: { color: colors[i % colors.length] },
      areaStyle: { color: translucent(colors[i % colors.length], 0.08) },
    })),
  };

  return (
    <ReactECharts
      option={option}
      style={{ height, width: "100%" }}
      notMerge
    />
  );
}
