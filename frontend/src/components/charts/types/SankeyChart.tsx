// ─── SankeyChart ──────────────────────────────────────────────────────────────
// Flow visualization — patient pathway, referral-to-admission pipeline.
// Data shape: { nodes: { name: string }[], links: { source, target, value }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface SankeyNode {
  name: string;
}

export interface SankeyLink {
  source: string;
  target: string;
  value: number;
}

export interface SankeyChartData {
  nodes: SankeyNode[];
  links: SankeyLink[];
}

interface SankeyChartProps {
  data: SankeyChartData;
  height?: number;
}

export function SankeyChart({ data, height = 260 }: SankeyChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 700,
    animationEasing: "cubicOut",
    color: colors,
    tooltip: {
      ...chartTooltip(palette),
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { dataType: string; data: { source?: string; target?: string; value?: number; name?: string } };
        if (p.dataType === "edge") {
          return `<b style="font-family:${CHART_FONT}">${p.data.source} → ${p.data.target}</b><br/>Value: ${p.data.value}`;
        }
        return `<b style="font-family:${CHART_FONT}">${p.data.name}</b>`;
      },
    },
    series: [{
      type: "sankey",
      data: data.nodes,
      links: data.links,
      orient: "horizontal",
      nodeAlign: "left",
      nodeWidth: 14,
      nodeGap: 12,
      draggable: false,
      label: {
        color: palette.ink,
        fontFamily: CHART_FONT,
        fontSize: 11,
      },
      lineStyle: {
        color: "gradient",
        opacity: 0.4,
      },
      itemStyle: {
        borderWidth: 0,
      },
      emphasis: {
        focus: "adjacency",
        lineStyle: { opacity: 0.7 },
      },
      levels: [
        { depth: 0, itemStyle: { color: translucent(palette.accent, 0.9) }, lineStyle: { opacity: 0.4 } },
        { depth: 1, itemStyle: { color: translucent(palette.green, 0.9) }, lineStyle: { opacity: 0.4 } },
        { depth: 2, itemStyle: { color: translucent(palette.amber, 0.9) }, lineStyle: { opacity: 0.4 } },
      ],
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
