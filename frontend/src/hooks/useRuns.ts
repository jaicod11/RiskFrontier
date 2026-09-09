/** Run the analyses, tracking loading, elapsed time and the backend's errors. */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { optimize, runBacktest, runBootstrap, runVar } from "../api/endpoints";
import {
  toWeightFractions,
  usePortfolio,
  type Holding,
  type RunParams,
} from "../store/portfolio";

export type RunKind = "risk" | "optimize" | "backtest";

interface RunState {
  running: RunKind | null;
  /** What the current run is doing, for the progress note. */
  stage: string;
  elapsedSeconds: number;
  error: ApiError | null;
}

function portfolioBody(holdings: Holding[], params: RunParams) {
  return {
    positions: holdings.map((holding) => ({
      ticker: holding.ticker,
      weight: holding.weightPct / 100,
    })),
    total_value_inr: params.totalValueInr,
  };
}

export function useRuns() {
  const { holdings, params, setResult } = usePortfolio();
  const [state, setState] = useState<RunState>({
    running: null,
    stage: "",
    elapsedSeconds: 0,
    error: null,
  });
  const startedAt = useRef<number | null>(null);

  // A visible elapsed counter is what separates "working" from "hung".
  useEffect(() => {
    if (!state.running) return;
    const timer = window.setInterval(() => {
      if (startedAt.current === null) return;
      setState((current) => ({
        ...current,
        elapsedSeconds: Math.round((Date.now() - startedAt.current!) / 1000),
      }));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [state.running]);

  const begin = (kind: RunKind, stage: string) => {
    startedAt.current = Date.now();
    setState({ running: kind, stage, elapsedSeconds: 0, error: null });
  };
  const succeed = () => {
    startedAt.current = null;
    setState({ running: null, stage: "", elapsedSeconds: 0, error: null });
  };
  const fail = (error: unknown) => {
    startedAt.current = null;
    setState({
      running: null,
      stage: "",
      elapsedSeconds: 0,
      error: error instanceof ApiError ? error : new ApiError(String(error), "UNKNOWN"),
    });
  };

  const clearError = useCallback(
    () => setState((current) => ({ ...current, error: null })),
    [],
  );

  const risk = useCallback(async () => {
    begin("risk", "Simulating");
    try {
      setResult(
        "var",
        await runVar({
          portfolio: portfolioBody(holdings, params),
          n_sims: params.nSims,
          horizon_days: params.horizonDays,
          lookback_days: params.lookbackDays,
          confidence_levels: [0.95, 0.99],
          seed: 42,
        }),
      );
      succeed();
      return true;
    } catch (error) {
      fail(error);
      return false;
    }
  }, [holdings, params, setResult]);

  const optimizeRun = useCallback(async () => {
    begin("optimize", "Solving the frontier");
    try {
      setResult(
        "optimize",
        await optimize({
          tickers: holdings.map((holding) => holding.ticker),
          risk_free_rate: params.riskFreeRate,
          max_weight_per_asset: params.maxWeightPerAsset,
          lookback_days: params.lookbackDays,
          n_frontier_points: params.nFrontierPoints,
          allow_short: false,
          seed: 0,
        }),
      );
      succeed();
      return true;
    } catch (error) {
      fail(error);
      return false;
    }
  }, [holdings, params, setResult]);

  const backtest = useCallback(async () => {
    begin("backtest", "Simulating day by day");
    try {
      const weights = toWeightFractions(holdings);
      const tickers = holdings.map((holding) => holding.ticker);

      setResult(
        "backtest",
        await runBacktest({
          tickers,
          target_weights: weights,
          start_date: params.startDate,
          end_date: params.endDate,
          initial_capital_inr: params.totalValueInr,
          rebalance_frequency: params.rebalanceFrequency,
          transaction_cost_bps: params.transactionCostBps,
          risk_free_rate: params.riskFreeRate,
        }),
      );

      if (params.runBootstrap) {
        setState((current) => ({
          ...current,
          stage: "Re-running across every historical window",
        }));
        setResult(
          "bootstrap",
          await runBootstrap({
            tickers,
            // Every field supplied: the generated types mark defaulted fields
            // as required, which keeps responses strict (the server always
            // sends them) at the cost of a slightly more explicit request.
            strategy: {
              kind: "constant_mix",
              target_weights: weights,
              lookback_days: params.lookbackDays,
              max_weight_per_asset: params.maxWeightPerAsset,
              objective: "max_sharpe",
              allow_short: false,
            },
            window_years: params.windowYears,
            method: params.bootstrapMethod,
            initial_capital_inr: params.totalValueInr,
            rebalance_frequency: params.rebalanceFrequency,
            transaction_cost_bps: params.transactionCostBps,
            risk_free_rate: params.riskFreeRate,
            n_resamples: 200,
            expected_block_days: 21,
            include_windows: true,
            seed: 0,
          }),
        );
      }
      succeed();
      return true;
    } catch (error) {
      fail(error);
      return false;
    }
  }, [holdings, params, setResult]);

  return { ...state, risk, optimize: optimizeRun, backtest, clearError };
}

/** Copy for the progress note, per run kind. */
export const RUN_DETAIL: Record<RunKind, string> = {
  risk: "Drawing simulated paths and compounding each one. Usually under a second.",
  optimize:
    "Running SLSQP from several starting points at every frontier target.",
  backtest:
    "Walking the price history one day at a time. If bootstrap is enabled this " +
    "repeats over every historical window and can take 30 seconds or more — " +
    "it is working, not stuck.",
};
