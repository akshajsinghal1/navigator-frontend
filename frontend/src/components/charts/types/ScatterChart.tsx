// ─── ScatterChart ─────────────────────────────────────────────────────────────
// Best for: correlation between two metrics (actual vs benchmark LOS).
// Data shape: { series: { name: string, data: [number, number][], color?: string }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface ScatterChartData {
  xLabel?: string;
  yLabel?: string;
  series: {
    name: string;
    data: [number, number][];
    color?: string;
  }[];
}

interface ScatterChartProps {
  data: ScatterChartData;
  height?: number;
}

export function ScatterChart({ data, height = 260 }: ScatterChartProps) {
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
    tooltip: { ...chartTooltip(palette), trigger: "item" },
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
        symbolSize: 9,
        itemStyle: {
          color: translucent(color, 0.85),
          borderColor: color,
          borderWidth: 1,
        },
        emphasis: { scale: 1.2 },
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
