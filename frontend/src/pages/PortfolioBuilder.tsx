import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { listSecurities } from "../api/endpoints";
import type { SecuritySummary } from "../api/types";
import { ErrorBanner, RunningNotice } from "../components/states";
import { TickerPicker } from "../components/TickerPicker";
import { Button, Field, NumberInput, Panel, Select, TextInput } from "../components/ui";
import { WeightsEditor } from "../components/WeightsEditor";
import { RUN_DETAIL, useRuns } from "../hooks/useRuns";
import { inr } from "../lib/format";
import { usePortfolio } from "../store/portfolio";

export default function PortfolioBuilder() {
  const navigate = useNavigate();
  const { holdings, setHoldings, params, setParams, totalWeightPct, weightsAreValid } =
    usePortfolio();
  const runs = useRuns();

  const [securities, setSecurities] = useState<SecuritySummary[]>([]);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listSecurities()
      .then(setSecurities)
      .catch((error) => setLoadError(error as ApiError))
      .finally(() => setLoading(false));
  }, []);

  const selected = useMemo(
    () => new Set(holdings.map((holding) => holding.ticker)),
    [holdings],
  );

  const toggle = useCallback(
    (ticker: string) => {
      const exists = holdings.some((holding) => holding.ticker === ticker);
      setHoldings(
        exists
          ? holdings.filter((holding) => holding.ticker !== ticker)
          : [...holdings, { ticker, weightPct: 0 }],
      );
    },
    [holdings, setHoldings],
  );

  const equalWeight = useCallback(() => {
    if (holdings.length === 0) return;
    // Give the remainder to the last holding so the total lands on exactly 100.
    const each = Math.floor((100 / holdings.length) * 100) / 100;
    setHoldings(
      holdings.map((holding, index) => ({
        ...holding,
        weightPct:
          index === holdings.length - 1
            ? Number((100 - each * (holdings.length - 1)).toFixed(2))
            : each,
      })),
    );
  }, [holdings, setHoldings]);

  const setWeight = useCallback(
    (ticker: string, weightPct: number) =>
      setHoldings(
        holdings.map((holding) =>
          holding.ticker === ticker ? { ...holding, weightPct } : holding,
        ),
      ),
    [holdings, setHoldings],
  );

  const busy = runs.running !== null;
  const canRun = weightsAreValid && !busy;
  const blockedReason = !holdings.length
    ? "Select at least one ticker."
    : !weightsAreValid
      ? "Weights must total 100%."
      : null;

  const go = async (kind: "risk" | "optimize" | "backtest") => {
    const ok = await (kind === "risk"
      ? runs.risk()
      : kind === "optimize"
        ? runs.optimize()
        : runs.backtest());
    if (ok) navigate("/results");
  };

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Portfolio builder</h1>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-slate-600 dark:text-slate-400">
          Pick a universe, set weights, then run any of the three analyses. Every
          result is returned with the caveats that qualify it.
        </p>
      </header>

      {loadError && <ErrorBanner error={loadError} />}
      {runs.error && <ErrorBanner error={runs.error} onDismiss={runs.clearError} />}
      {busy && (
        <RunningNotice
          label={runs.stage}
          detail={RUN_DETAIL[runs.running!]}
          elapsedSeconds={runs.elapsedSeconds}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <Panel
          title="Universe"
          subtitle={
            loading ? "Loading securities…" : `${securities.length} securities ingested`
          }
        >
          {loading ? (
            <p className="py-8 text-center text-xs text-slate-500">Loading…</p>
          ) : (
            <TickerPicker
              securities={securities}
              selected={selected}
              onToggle={toggle}
            />
          )}
        </Panel>

        <Panel title="Holdings">
          <WeightsEditor
            holdings={holdings}
            totalPct={totalWeightPct}
            onChange={setWeight}
            onRemove={toggle}
            onEqualWeight={equalWeight}
          />
        </Panel>
      </div>

      <Panel title="Parameters">
        <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Portfolio value (₹)" hint={inr(params.totalValueInr)}>
            <NumberInput
              value={params.totalValueInr}
              step={100000}
              min={1}
              onChange={(totalValueInr) => setParams({ totalValueInr })}
            />
          </Field>
          <Field label="Lookback (trading days)" hint="Estimation window for risk and optimiser">
            <NumberInput
              value={params.lookbackDays}
              step={21}
              min={30}
              onChange={(lookbackDays) => setParams({ lookbackDays })}
            />
          </Field>
          <Field label="Risk-free rate" hint="Annualised fraction, e.g. 0.065">
            <NumberInput
              value={params.riskFreeRate}
              step={0.005}
              min={0}
              onChange={(riskFreeRate) => setParams({ riskFreeRate })}
            />
          </Field>
          <Field label="Max weight per asset" hint="Optimiser constraint, e.g. 0.35">
            <NumberInput
              value={params.maxWeightPerAsset}
              step={0.05}
              min={0.01}
              max={1}
              onChange={(maxWeightPerAsset) => setParams({ maxWeightPerAsset })}
            />
          </Field>

          <Field label="Simulations" hint="Monte Carlo paths">
            <NumberInput
              value={params.nSims}
              step={1000}
              min={100}
              onChange={(nSims) => setParams({ nSims })}
            />
          </Field>
          <Field label="Horizon (trading days)" hint="VaR forecast horizon">
            <NumberInput
              value={params.horizonDays}
              step={1}
              min={1}
              onChange={(horizonDays) => setParams({ horizonDays })}
            />
          </Field>
          <Field label="Frontier points">
            <NumberInput
              value={params.nFrontierPoints}
              step={5}
              min={2}
              onChange={(nFrontierPoints) => setParams({ nFrontierPoints })}
            />
          </Field>
          <Field label="Transaction cost (bps/leg)" hint="Backtest only">
            <NumberInput
              value={params.transactionCostBps}
              step={1}
              min={0}
              onChange={(transactionCostBps) => setParams({ transactionCostBps })}
            />
          </Field>

          <Field label="Backtest start">
            <TextInput
              type="date"
              value={params.startDate}
              onChange={(startDate) => setParams({ startDate })}
            />
          </Field>
          <Field label="Backtest end">
            <TextInput
              type="date"
              value={params.endDate}
              onChange={(endDate) => setParams({ endDate })}
            />
          </Field>
          <Field label="Rebalance">
            <Select
              value={params.rebalanceFrequency}
              onChange={(rebalanceFrequency) => setParams({ rebalanceFrequency })}
              options={[
                { value: "monthly", label: "Monthly" },
                { value: "quarterly", label: "Quarterly" },
              ]}
            />
          </Field>
          <Field label="Bootstrap window (years)" hint="Only used when bootstrap is on">
            <NumberInput
              value={params.windowYears}
              step={0.5}
              min={1}
              onChange={(windowYears) => setParams({ windowYears })}
            />
          </Field>
        </div>

        <label className="mt-3 flex items-start gap-2 border-t border-slate-200 pt-3 dark:border-slate-800">
          <input
            type="checkbox"
            checked={params.runBootstrap}
            onChange={(event) => setParams({ runBootstrap: event.target.checked })}
            className="mt-0.5"
          />
          <span className="text-xs leading-relaxed text-slate-700 dark:text-slate-300">
            <span className="font-medium">
              Also bootstrap across historical windows
            </span>
            <span className="block text-slate-500">
              Re-runs the backtest over every {params.windowYears}-year window and
              reports the distribution instead of one number. Adds 30 seconds or
              more to the run.
            </span>
          </span>
        </label>
      </Panel>

      <Panel title="Run">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={!canRun} onClick={() => go("risk")}>
            Run risk analysis
          </Button>
          <Button disabled={!canRun} onClick={() => go("optimize")}>
            Optimize
          </Button>
          <Button disabled={!canRun} onClick={() => go("backtest")}>
            Run backtest{params.runBootstrap ? " + bootstrap" : ""}
          </Button>
          {blockedReason && (
            <span className="text-xs text-amber-700 dark:text-amber-400">
              {blockedReason}
            </span>
          )}
          <span className="ml-auto">
            <Button variant="ghost" onClick={() => navigate("/results")}>
              View results →
            </Button>
          </span>
        </div>
      </Panel>
    </div>
  );
}
