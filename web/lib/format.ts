// Number and date formatting used everywhere, so every figure carries units the same way.

const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export const money = (v: number) => (Math.abs(v) >= 1000 ? usd0.format(v) : usd2.format(v));
export const moneyCompact = (v: number) => `$${compact.format(v)}`;
export const count = (v: number) => (Math.abs(v) >= 10_000 ? compact.format(v) : Math.round(v).toLocaleString("en-US"));
export const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;
export const ms = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v.toFixed(v < 10 ? 1 : 0)} ms`);

export function formatUnit(v: number, unit: "count" | "seconds" | "ratio" | "usd"): string {
  switch (unit) {
    case "usd":
      return v < 1 && v > 0 ? `$${v.toFixed(4)}` : money(v);
    case "ratio":
      return pct(v, 1);
    case "seconds":
      return ms(v * 1000);
    default:
      return count(v);
  }
}

export function date(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function dateTime(iso: string | number): string {
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return Number.isNaN(d.getTime())
    ? String(iso)
    : d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function timeTick(t: number, rangeSeconds: number): string {
  const d = new Date(t * 1000);
  return rangeSeconds > 2 * 86400
    ? d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  pending_manager: "Waiting for manager",
  pending_finance: "Waiting for Finance",
  pending_admin: "Waiting for admin",
  approved: "Approved",
  rejected: "Rejected",
  ordered: "Ordered",
  cancelled: "Cancelled",
};
export const statusLabel = (s: string) => STATUS_LABEL[s] ?? s;

export const levelLabel = (l: string) => ({ manager: "Manager", finance: "Finance", admin: "Admin" })[l] ?? l;
