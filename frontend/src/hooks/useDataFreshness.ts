/**
 * useDataFreshness
 * ────────────────
 * Polls GET /api/freshness/{companyId} every 5 minutes.
 * Returns the current data_version, last_refreshed_at, and a boolean
 * `dataUpdated` that is true when the version changed since mount.
 *
 * The parent component watches `dataUpdated` and re-fetches the
 * Intelligence Config when it flips to true.
 */
import { useEffect, useRef, useState } from "react";

const POLL_MS = 5 * 60 * 1000; // 5 minutes

interface FreshnessInfo {
  data_version: number;
  last_refreshed_at: string | null;
  status: "fresh" | "refreshing" | "unknown";
}

interface UseFreshnessResult {
  dataVersion: number;
  lastRefreshedAt: string | null;
  status: string;
  dataUpdated: boolean;          // true when version changed since mount
  clearDataUpdated: () => void;  // call after re-fetching config
}

export function useDataFreshness(companyId: string | null): UseFreshnessResult {
  const [info, setInfo] = useState<FreshnessInfo>({
    data_version: 1,
    last_refreshed_at: null,
    status: "unknown",
  });
  const [dataUpdated, setDataUpdated] = useState(false);
  const baseVersionRef = useRef<number | null>(null);

  // eslint-disable-next-line react-doctor/no-fetch-in-effect -- intentional polling hook; no react-query/SWR dependency available
  useEffect(() => {
    if (!companyId) return;

    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`/api/freshness/${encodeURIComponent(companyId!)}`);
        if (!res.ok || cancelled) return;
        const data: FreshnessInfo = await res.json();

        if (baseVersionRef.current === null) {
          // First poll — establish baseline
          baseVersionRef.current = data.data_version;
        } else if (data.data_version > baseVersionRef.current) {
          // Version advanced — signal re-fetch
          setDataUpdated(true);
          baseVersionRef.current = data.data_version;
        }

        if (!cancelled) setInfo(data);
      } catch {
        // Silently ignore network errors — freshness is best-effort
      }
    }

    // Poll immediately on mount, then every POLL_MS
    poll();
    const timer = setInterval(poll, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [companyId]);

  return {
    dataVersion:     info.data_version,
    lastRefreshedAt: info.last_refreshed_at,
    status:          info.status,
    dataUpdated,
    clearDataUpdated: () => setDataUpdated(false),
  };
}
