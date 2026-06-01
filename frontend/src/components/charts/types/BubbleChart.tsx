// ─── BubbleChart ─────────────────────────────────────────────────────────────
// Scatter plot with a third dimension represented by bubble size.
// Data shape: { series: { name: string, data: [x, y, size][], color?: string }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface BubbleChartData {
  xLabel?: string;
  yLabel?: string;
  series: {
    name: string;
    data: [number, number, number][];
    color?: string;
  }[];
}

interface BubbleChartProps {
  data: BubbleChartData;
  height?: number;
}

export function BubbleChart({ data, height = 260 }: BubbleChartProps) {
  const { palette } = useChartTheme();
  const defaultColors = [palette.accent, palette.green, palette.amber, palette.red];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 650,
    animationEasing: "cubicOut",
    grid: { top: 36, right: 18, bottom: 40, left: 46 },
    legend: {
      top: 0, right: 0,
      itemWidth: 10, itemHeight: 10,
      textStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    },
    tooltip: {
      ...chartTooltip(palette),
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { seriesName: string; value: [number, number, number]; marker: string };
        return `${p.marker} ${p.seriesName}<br/>X: ${p.value[0]}<br/>Y: ${p.value[1]}<br/>Size: ${p.value[2]}`;
      },
    },
    xAxis: {
      type: "value",
      name: data.xLabel ?? "X",
      nameGap: 28,
      nameLocation: "middle",
      nameTextStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.line2 } },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: palette.line, type: "dashed" } },
    },
    yAxis: {
      type: "value",
      name: data.yLabel ?? "Y",
      nameTextStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.line2 } },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: palette.line, type: "dashed" } },
    },
    series: data.series.map((s, i) => {
      const color = s.color ?? defaultColors[i % defaultColors.length];
      return {
        name: s.name,
        type: "scatter",
        data: s.data,
        symbolSize: (val: number[]) => Math.max(8, Math.sqrt(val[2]) * 4),
        itemStyle: {
          color: translucent(color, 0.75),
          borderColor: color,
          borderWidth: 1.5,
        },
        emphasis: { scale: 1.15 },
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
