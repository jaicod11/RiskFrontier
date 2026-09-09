/** Indian-numbering formatters. Raw floats are unreadable at these magnitudes. */

const LAKH = 100_000;
const CRORE = 10_000_000;

/** Full precision with Indian digit grouping: 12,34,567. */
export function inr(value: number, fractionDigits = 0): string {
  if (!Number.isFinite(value)) return "—";
  return `₹${value.toLocaleString("en-IN", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}`;
}

/** Compact for chart axes and tight cells: ₹12.3L, ₹1.23Cr. */
export function inrCompact(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const magnitude = Math.abs(value);

  if (magnitude >= CRORE) return `${sign}₹${(magnitude / CRORE).toFixed(2)}Cr`;
  if (magnitude >= LAKH) return `${sign}₹${(magnitude / LAKH).toFixed(2)}L`;
  if (magnitude >= 1000) return `${sign}₹${(magnitude / 1000).toFixed(1)}K`;
  return `${sign}₹${magnitude.toFixed(0)}`;
}

/** A fraction (0.0713) as a percentage string (7.13%). */
export function pct(value: number, fractionDigits = 2): string {
  if (!Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

/** A value already expressed in percentage points (7.13 -> 7.13%). */
export function pctPoints(value: number, fractionDigits = 2): string {
  if (!Number.isFinite(value)) return "—";
  return `${value.toFixed(fractionDigits)}%`;
}

export function ratio(value: number, fractionDigits = 3): string {
  if (!Number.isFinite(value)) return "—";
  return value.toFixed(fractionDigits);
}

export function shortDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** Tailwind class for a number where negative is bad — used for returns. */
export function signClass(value: number): string {
  if (!Number.isFinite(value) || value === 0) return "text-slate-700 dark:text-slate-300";
  return value > 0
    ? "text-emerald-700 dark:text-emerald-400"
    : "text-rose-700 dark:text-rose-400";
}

/** Losses are reported as positive magnitudes; they are always bad. */
export const LOSS_CLASS = "text-rose-700 dark:text-rose-400";
