/** One palette for every chart, so a series means the same thing everywhere. */
export const SERIES_COLOR = {
  strategy: "#2563eb",
  buy_and_hold_same_stocks: "#64748b",
  buy_and_hold_nifty50: "#d97706",
} as const;

export const METHOD_COLOR = {
  parametric: "#64748b",
  historical_bootstrap: "#2563eb",
} as const;

export const GRID = "#e2e8f0";
export const AXIS = "#94a3b8";

export const AXIS_TICK = { fontSize: 11, fill: AXIS };

export const TOOLTIP_STYLE = {
  contentStyle: {
    border: "1px solid #cbd5e1",
    borderRadius: 0,
    fontSize: 12,
    padding: "6px 8px",
    backgroundColor: "#ffffff",
  },
  labelStyle: { fontSize: 11, color: "#64748b", marginBottom: 2 },
} as const;
