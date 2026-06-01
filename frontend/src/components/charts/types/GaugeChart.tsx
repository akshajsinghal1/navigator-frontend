// ─── GaugeChart ───────────────────────────────────────────────────────────────
// KPI and progress indicator — single metric vs target.
// Data shape: { value: number, min?: number, max?: number, label?: string, unit?: string }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, translucent } from "../chartTheme";

export interface GaugeChartData {
  value: number;
  min?: number;
  max?: number;
  label?: string;
  unit?: string;
  thresholds?: { value: number; color: string }[];
}

interface GaugeChartProps {
  data: GaugeChartData;
  height?: number;
}

export function GaugeChart({ data, height = 260 }: GaugeChartProps) {
  const { palette } = useChartTheme();
  const min = data.min ?? 0;
  const max = data.max ?? 100;
  const pct = ((data.value - min) / (max - min)) * 100;

  // Determine color from thresholds or auto-calculate from value %
  let color = palette.green;
  if (pct < 60) color = palette.red;
  else if (pct < 80) color = palette.amber;
  if (data.thresholds) {
    for (const t of [...data.thresholds].reverse()) {
      if (data.value >= t.value) { color = t.color; break; }
    }
  }

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 800,
    animationEasing: "cubicOut",
    series: [{
      type: "gauge",
      min,
      max,
      startAngle: 210,
      endAngle: -30,
      radius: "85%",
      center: ["50%", "60%"],
      pointer: { show: false },
      progress: {
        show: true,
        width: 14,
        roundCap: true,
        itemStyle: { color },
      },
      axisLine: {
        lineStyle: {
          width: 14,
          color: [[1, translucent(palette.bg3, 0.8)]],
        },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: {
        valueAnimation: true,
        formatter: (val: number) => `${val.toFixed(1)}${data.unit ?? ""}`,
        color: palette.ink,
        fontFamily: CHART_NUM_FONT,
        fontSize: 26,
        fontWeight: "bold",
        offsetCenter: [0, "10%"],
      },
      title: {
        show: true,
        offsetCenter: [0, "50%"],
        color: palette.ink3,
        fontFamily: CHART_FONT,
        fontSize: 11,
      },
      data: [{ value: data.value, name: data.label ?? "" }],
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
