// Status types (previously in types/api, now kept local to chartTheme)
type Status = "red" | "amber" | "green";
type StatusCss = "r" | "a" | "g";

export interface ChartPalette {
  bg: string;
  bg1: string;
  bg2: string;
  bg3: string;
  line: string;
  line2: string;
  ink: string;
  ink2: string;
  ink3: string;
  ink4: string;
  accent: string;
  red: string;
  amber: string;
  green: string;
}

const FALLBACK: ChartPalette = {
  bg: "#0B0C0E",
  bg1: "#101114",
  bg2: "#16181c",
  bg3: "#1d2025",
  line: "#1e2126",
  line2: "#2a2e36",
  ink: "#EDEDEA",
  ink2: "#A9ACAF",
  ink3: "#6C6F74",
  ink4: "#44474d",
  accent: "#E8A33A",
  red: "#E5484D",
  amber: "#F0C040",
  green: "#5BAD7A",
};

const CSS_VAR_BY_KEY: Record<keyof ChartPalette, string> = {
  bg: "--bg",
  bg1: "--bg-1",
  bg2: "--bg-2",
  bg3: "--bg-3",
  line: "--line",
  line2: "--line-2",
  ink: "--ink",
  ink2: "--ink-2",
  ink3: "--ink-3",
  ink4: "--ink-4",
  accent: "--persona-accent",
  red: "--red",
  amber: "--amber",
  green: "--green",
};

export const CHART_FONT =
  "'Inter Tight', ui-sans-serif, system-ui, sans-serif";
export const CHART_NUM_FONT =
  "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";

function resolveCssValue(styles: CSSStyleDeclaration, value: string, fallback: string): string {
  let next = value.trim();
  for (let i = 0; i < 4; i += 1) {
    const match = next.match(/^var\((--[^,\s)]+)(?:,\s*([^)]+))?\)$/);
    if (!match) break;
    next = (styles.getPropertyValue(match[1]).trim() || match[2] || fallback).trim();
  }
  return next || fallback;
}

export function getChartPalette(): ChartPalette {
  if (typeof window === "undefined") return FALLBACK;

  const styles = window.getComputedStyle(document.documentElement);
  const next = { ...FALLBACK };

  (Object.keys(CSS_VAR_BY_KEY) as Array<keyof ChartPalette>).forEach((key) => {
    const value = styles.getPropertyValue(CSS_VAR_BY_KEY[key]);
    if (value) next[key] = resolveCssValue(styles, value, FALLBACK[key]);
  });

  return next;
}

export function statusColor(status: Status | StatusCss, palette: ChartPalette): string {
  if (status === "red" || status === "r") return palette.red;
  if (status === "amber" || status === "a") return palette.amber;
  return palette.green;
}

export function translucent(hex: string, alpha: number): string {
  if (!hex.startsWith("#") || hex.length !== 7) return hex;
  const r = Number.parseInt(hex.slice(1, 3), 16);
  const g = Number.parseInt(hex.slice(3, 5), 16);
  const b = Number.parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function chartTooltip(palette: ChartPalette) {
  return {
    backgroundColor: palette.bg2,
    borderColor: palette.line2,
    borderWidth: 1,
    padding: [9, 11],
    extraCssText: "box-shadow: 0 18px 50px -20px rgba(0,0,0,.65); border-radius: 4px;",
    textStyle: {
      color: palette.ink,
      fontFamily: CHART_NUM_FONT,
      fontSize: 11,
      lineHeight: 18,
    },
  };
}

export function stripHtml(value: string): string {
  return value.replace(/<[^>]+>/g, "");
}
