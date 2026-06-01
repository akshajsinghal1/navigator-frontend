// ─── StackedBarChart ─────────────────────────────────────────────────────────
// Best for: showing part-to-whole breakdown over categories (payer mix by facility).
// Data shape: { labels: string[], series: { name: string, data: number[] }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_NUM_FONT, chartTooltip } from "../chartTheme";

export interface StackedBarChartData {
  labels: string[];
  series: { name: string; data: number[] }[];
  horizontal?: boolean;
}

interface StackedBarChartProps {
  data: StackedBarChartData;
  height?: number;
}

export function StackedBarChart({ data, height = 260 }: StackedBarChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2, palette.ink3];
  const horizontal = data.horizontal ?? false;

  const categoryAxis = {
    type: "category" as const,
    data: data.labels,
    axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    axisLine: { lineStyle: { color: palette.line2 } },
    axisTick: { show: false },
    splitLine: { show: false },
  };

  const valueAxis = {
    type: "value" as const,
    axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: palette.line, type: "dashed" as const } },
  };

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 650,
    animationEasing: "cubicOut",
    color: colors,
    grid: { top: 36, bottom: 32, left: horizontal ? 100 : 46, right: 18 },
    legend: {
      top: 0, right: 0,
      itemWidth: 10, itemHeight: 10,
      textStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    },
    tooltip: { ...chartTooltip(palette), trigger: "axis" },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
    series: data.series.map((s, i) => ({
      name: s.name,
      type: "bar",
      stack: "total",
      data: s.data,
      barMaxWidth: 40,
      itemStyle: { color: colors[i % colors.length] },
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
