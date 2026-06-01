// ─── CandlestickChart ─────────────────────────────────────────────────────────
// Financial OHLC visualization (Open/High/Low/Close).
// Data shape: { labels: string[], data: [open, close, low, high][] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_NUM_FONT, chartTooltip } from "../chartTheme";

export interface CandlestickChartData {
  labels: string[];
  // ECharts candlestick format: [open, close, low, high]
  data: [number, number, number, number][];
}

interface CandlestickChartProps {
  data: CandlestickChartData;
  height?: number;
}

export function CandlestickChart({ data, height = 260 }: CandlestickChartProps) {
  const { palette } = useChartTheme();

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 650,
    animationEasing: "cubicOut",
    grid: { top: 18, bottom: 32, left: 56, right: 18 },
    tooltip: {
      ...chartTooltip(palette),
      trigger: "axis",
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const p = list[0] as { name: string; value: [number, number, number, number] };
        const [open, close, low, high] = p.value;
        return [
          `<b style="font-family:${CHART_NUM_FONT}">${p.name}</b>`,
          `Open: ${open}`, `Close: ${close}`,
          `Low: ${low}`, `High: ${high}`,
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
      type: "candlestick",
      data: data.data,
      itemStyle: {
        color: palette.green,
        color0: palette.red,
        borderColor: palette.green,
        borderColor0: palette.red,
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
