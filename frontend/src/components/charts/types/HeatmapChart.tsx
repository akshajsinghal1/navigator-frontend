// ─── HeatmapChart ─────────────────────────────────────────────────────────────
// Shows intensity using color grids (staffing by shift, risk by day).
// Data shape: { xLabels: string[], yLabels: string[], data: [x, y, value][] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface HeatmapChartData {
  xLabels: string[];
  yLabels: string[];
  data: [number, number, number][];
  minValue?: number;
  maxValue?: number;
}

interface HeatmapChartProps {
  data: HeatmapChartData;
  height?: number;
}

export function HeatmapChart({ data, height = 260 }: HeatmapChartProps) {
  const { palette } = useChartTheme();
  const minVal = data.minValue ?? Math.min(...data.data.map((d) => d[2]));
  const maxVal = data.maxValue ?? Math.max(...data.data.map((d) => d[2]));

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 650,
    animationEasing: "cubicOut",
    grid: { top: 18, right: 16, bottom: 28, left: 100 },
    tooltip: {
      ...chartTooltip(palette),
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { data: [number, number, number] };
        const [xi, yi, val] = p.data;
        return [
          `<b style="font-family:${CHART_FONT}">${data.yLabels[yi]}</b>`,
          `${data.xLabels[xi]}: ${val}`,
        ].join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: data.xLabels,
      splitArea: { show: false },
      axisLabel: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.line2 } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "category",
      data: data.yLabels,
      inverse: true,
      splitArea: { show: false },
      axisLabel: { color: palette.ink2, fontFamily: CHART_FONT, fontSize: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    visualMap: {
      show: false,
      min: minVal,
      max: maxVal,
      inRange: {
        color: [
          translucent(palette.red, 0.9),
          translucent(palette.amber, 0.9),
          translucent(palette.green, 0.85),
        ],
      },
    },
    series: [{
      type: "heatmap",
      data: data.data,
      label: {
        show: true,
        color: "#0B0C0E",
        fontFamily: CHART_NUM_FONT,
        fontSize: 10,
        formatter: (params: unknown) => {
          const p = params as { data: [number, number, number] };
          return String(Math.round(p.data[2]));
        },
      },
      itemStyle: {
        borderColor: palette.bg1,
        borderWidth: 2,
        borderRadius: 2,
      },
      emphasis: {
        itemStyle: { borderColor: palette.accent, borderWidth: 2 },
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
