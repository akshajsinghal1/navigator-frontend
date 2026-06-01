// ─── ChartThemeContext ───────────────────────────────────────────────────────
// Provides the ECharts color palette to all chart components via React context.
// Listens for `data-theme` mutations on <html> so charts re-render automatically
// when the user switches theme in TweaksPanel — without any component unmounting.

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { getChartPalette, type ChartPalette } from "../components/charts/chartTheme";

interface ChartThemeContextValue {
  palette: ChartPalette;
}

const ChartThemeContext = createContext<ChartThemeContextValue>({
  palette: getChartPalette(),
});

/** Wrap your app (or the canvas section) with this provider. */
export function ChartThemeProvider({ children }: { children: ReactNode }) {
  const [palette, setPalette] = useState<ChartPalette>(getChartPalette);

  useEffect(() => {
    // Re-read palette whenever the data-theme or data-persona attribute changes
    const observer = new MutationObserver(() => {
      setPalette(getChartPalette());
    });

    // eslint-disable-next-line react-doctor/no-initialize-state
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "data-persona"],
    });

    return () => observer.disconnect();
  }, []);

  const value = useMemo(() => ({ palette }), [palette]);

  return (
    <ChartThemeContext.Provider value={value}>
      {children}
    </ChartThemeContext.Provider>
  );
}

/**
 * Hook for chart components to consume the current palette.
 * Usage: const { palette } = useChartTheme();
 */
export function useChartTheme(): ChartThemeContextValue {
  return useContext(ChartThemeContext);
}
