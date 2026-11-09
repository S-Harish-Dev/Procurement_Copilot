import type { Series } from "./types";

/** Merge named [[t, v]] series into Recharts rows keyed by time: [{t, a, b}, ...]. */
export function mergeSeries(named: Record<string, Series>): Record<string, number>[] {
  const byT = new Map<number, Record<string, number>>();
  for (const [name, series] of Object.entries(named)) {
    for (const [t, v] of series) {
      const row = byT.get(t) ?? { t };
      row[name] = v;
      byT.set(t, row);
    }
  }
  return [...byT.values()].sort((a, b) => a.t - b.t);
}

export const sumSeries = (s: Series) => s.reduce((a, [, v]) => a + (Number.isFinite(v) ? v : 0), 0);

export const RANGE_SECONDS: Record<string, number> = { "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800 };
