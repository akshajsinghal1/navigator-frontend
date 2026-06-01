// ─── GraphChart ───────────────────────────────────────────────────────────────
// Node-link relationship visualization (dependency graphs, referral networks).
// Data shape: { nodes: { id, name, value?, category? }[], links: { source, target, value? }[] }

import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useChartTheme } from "../../../context/ChartThemeContext";
import { CHART_FONT, CHART_NUM_FONT, chartTooltip, translucent } from "../chartTheme";

export interface GraphNode {
  id: string;
  name: string;
  value?: number;
  category?: number;
}

export interface GraphLink {
  source: string;
  target: string;
  value?: number;
}

export interface GraphChartData {
  nodes: GraphNode[];
  links: GraphLink[];
  categories?: { name: string }[];
}

interface GraphChartProps {
  data: GraphChartData;
  height?: number;
}

export function GraphChart({ data, height = 260 }: GraphChartProps) {
  const { palette } = useChartTheme();
  const colors = [palette.accent, palette.green, palette.amber, palette.red, palette.ink2];

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 700,
    animationEasing: "cubicOut",
    color: colors,
    tooltip: {
      ...chartTooltip(palette),
      formatter: (params: unknown) => {
        const p = params as { data: { name?: string; source?: string; target?: string }; dataType: string };
        if (p.dataType === "edge") {
          return `<b style="font-family:${CHART_FONT}">${p.data.source} → ${p.data.target}</b>`;
        }
        return `<b style="font-family:${CHART_FONT}">${p.data.name}</b>`;
      },
    },
    legend: data.categories ? [{
      data: data.categories.map((c) => c.name),
      textStyle: { color: palette.ink3, fontFamily: CHART_NUM_FONT, fontSize: 10 },
    }] : undefined,
    series: [{
      type: "graph",
      layout: "force",
      data: data.nodes,
      links: data.links,
      categories: data.categories,
      roam: true,
      draggable: true,
      symbolSize: 18,
      label: {
        show: true,
        position: "right",
        color: palette.ink2,
        fontFamily: CHART_FONT,
        fontSize: 10,
      },
      edgeSymbol: ["none", "arrow"],
      edgeSymbolSize: 6,
      lineStyle: {
        color: translucent(palette.ink3, 0.4),
        curveness: 0.2,
      },
      force: { repulsion: 120, edgeLength: 80 },
      emphasis: {
        scale: true,
        focus: "adjacency",
        itemStyle: { shadowBlur: 18, shadowColor: "rgba(0,0,0,.35)" },
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
