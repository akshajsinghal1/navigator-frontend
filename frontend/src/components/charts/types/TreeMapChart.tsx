// ─── TreeMapChart ─────────────────────────────────────────────────────────────
// Hierarchical data using nested rectangles (DRG mix, department spend).
// Data shape: { data: { name: string, value: number, children?: ... }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface TreeMapNode {
  name: string;
  value: number;
  children?: TreeMapNode[];
}

export interface TreeMapChartData {
  data: TreeMapNode[];
}

interface TreeMapChartProps {
  data: TreeMapChartData;
  height?: number;
}

export function TreeMapChart({ data, height = 260 }: TreeMapChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 650,
    animationEasing: "cubicOut",
    tooltip: {
      ...chartTooltip(palette),
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number; marker: string };
        return `${p.marker} <b style="font-family:${CHART_FONT}">${p.name}</b><br/>Value: ${p.value}`;
      },
    },
    series: [{
      type: "treemap",
      data: data.data,
      width: "100%",
      height: "100%",
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: {
        show: true,
        color: palette.bg,
        fontFamily: CHART_FONT,
        fontSize: 11,
        overflow: "truncate",
      },
      itemStyle: {
        borderColor: palette.bg1,
        borderWidth: 2,
        gapWidth: 2,
      },
      levels: [
        {
          colorSaturation: [0.7, 0.95],
          itemStyle: { borderWidth: 2, borderColor: palette.bg1 },
          label: { show: true, fontSize: 12 },
        },
      ],
      color: colors.map((c) => translucent(c, 0.9)),
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
