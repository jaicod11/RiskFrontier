/**
 * Convenience aliases over the generated schema.
 *
 * Everything here is derived from `schema.d.ts`, which is generated from the
 * backend's own /openapi.json (`npm run gen:types`). Nothing in the frontend
 * hand-writes an API shape, so a backend change that breaks a contract shows up
 * as a type error rather than as a runtime surprise.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type ErrorResponse = Schemas["ErrorResponse"];
export type DataWindow = Schemas["DataWindow"];

export type SecuritySummary = Schemas["SecuritySummary"];
export type PriceSeries = Schemas["PriceSeries"];

export type Portfolio = Schemas["Portfolio"];
export type Position = Schemas["Position"];

export type VarRequest = Schemas["VarRequest"];
export type VarResponse = Schemas["VarResponse"];
export type VarEstimate = Schemas["VarEstimate"];
export type MethodResult = Schemas["MethodResult"];
export type PnlDistribution = Schemas["PnlDistribution"];

export type OptimizeRequest = Schemas["OptimizeRequest"];
export type OptimizeResponse = Schemas["OptimizeResponse"];
export type OptimizedPortfolio = Schemas["OptimizedPortfolio"];

export type BacktestRequest = Schemas["BacktestRequest"];
export type BacktestResponse = Schemas["BacktestResponse"];
export type BacktestSeries = Schemas["BacktestSeries"];
export type PerformanceMetrics = Schemas["PerformanceMetrics"];
export type ValuePoint = Schemas["ValuePoint"];

export type BootstrapRequest = Schemas["BootstrapRequest"];
export type BootstrapResponse = Schemas["BootstrapResponse"];
export type MetricSummary = Schemas["MetricSummaryOut"];
export type WinRates = Schemas["WinRatesOut"];
export type WindowResult = Schemas["WindowResultOut"];

export type HealthStatus = Schemas["HealthStatus"];

/** The three series every backtest and bootstrap reports on. */
export const SERIES_KEYS = [
  "strategy",
  "buy_and_hold_same_stocks",
  "buy_and_hold_nifty50",
] as const;
export type SeriesKey = (typeof SERIES_KEYS)[number];

export const SERIES_LABELS: Record<SeriesKey, string> = {
  strategy: "Strategy",
  buy_and_hold_same_stocks: "Hold same stocks",
  buy_and_hold_nifty50: "Nifty 50",
};
