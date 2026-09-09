import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  BacktestResponse,
  BootstrapResponse,
  PerformanceMetrics,
  SeriesKey,
} from "../../api/types";
import { SERIES_KEYS, SERIES_LABELS } from "../../api/types";
import { inr, inrCompact, pct, pctPoints, ratio, signClass } from "../../lib/format";
import { DataWindowNote } from "../DataWindowNote";
import { Limitations, selectBenchmarkCaveats } from "../Limitations";
import { Panel } from "../ui";
import { AXIS_TICK, GRID, SERIES_COLOR, TOOLTIP_STYLE } from "./chartTheme";

type Row = { key: SeriesKey; metrics: PerformanceMetrics };

function metricRows(data: BacktestResponse): Row[] {
  const rows: Row[] = [
    { key: "strategy", metrics: data.strategy.metrics },
    {
      key: "buy_and_hold_same_stocks",
      metrics: data.buy_and_hold_same_stocks.metrics,
    },
  ];
  if (data.buy_and_hold_nifty50) {
    rows.push({
      key: "buy_and_hold_nifty50",
      metrics: data.buy_and_hold_nifty50.metrics,
    });
  }
  return rows;
}

export function BacktestSection({
  data,
  bootstrap,
}: {
  data: BacktestResponse;
  bootstrap?: BootstrapResponse;
}) {
  const rows = metricRows(data);

  // One row per date, one column per series, so recharts can draw three lines.
  const chartData = useMemo(() => {
    const byDate = new Map<string, Record<string, number | string>>();
    const series: [SeriesKey, { values: { date: string; value_inr: number }[] } | null | undefined][] = [
      ["strategy", data.strategy],
      ["buy_and_hold_same_stocks", data.buy_and_hold_same_stocks],
      ["buy_and_hold_nifty50", data.buy_and_hold_nifty50],
    ];
    for (const [key, entry] of series) {
      if (!entry) continue;
      for (const point of entry.values) {
        const row = byDate.get(point.date) ?? { date: point.date };
        row[key] = point.value_inr;
        byDate.set(point.date, row);
      }
    }
    return [...byDate.values()].sort((a, b) =>
      String(a.date).localeCompare(String(b.date)),
    );
  }, [data]);

  const strategyCagr = data.strategy.metrics.cagr;
  const benchmarkCagr = data.buy_and_hold_nifty50?.metrics.cagr;
  const beatIndex =
    benchmarkCagr !== undefined ? strategyCagr > benchmarkCagr : null;

  // These caveats belong beside the comparison they undercut, not in a footer.
  const benchmarkCaveats = selectBenchmarkCaveats(data.limitations);
  const otherCaveats = (data.limitations ?? []).filter(
    (item) => !benchmarkCaveats.includes(item),
  );

  return (
    <Panel
      title="Backtest — strategy vs baselines"
      subtitle={
        <>
          {inr(data.initial_capital_inr)} initial ·{" "}
          {data.rebalance_frequency} rebalance ·{" "}
          <span className="tabular">{data.transaction_cost_bps}</span> bps per leg
        </>
      }
    >
      <div className="space-y-4">
        <div className="h-80 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 4, right: 12, bottom: 4, left: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis
                dataKey="date"
                tick={AXIS_TICK}
                tickLine={false}
                minTickGap={48}
                tickFormatter={(value) => String(value).slice(0, 7)}
              />
              <YAxis
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                width={64}
                domain={["auto", "auto"]}
                tickFormatter={(value) => inrCompact(Number(value))}
              />
              <Tooltip
                {...TOOLTIP_STYLE}
                formatter={(value, name) => [
                  inr(Number(value)),
                  SERIES_LABELS[name as SeriesKey] ?? String(name),
                ]}
              />
              <Legend
                wrapperStyle={{ fontSize: 11 }}
                formatter={(value) => SERIES_LABELS[value as SeriesKey] ?? value}
              />
              {SERIES_KEYS.map((key) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={SERIES_COLOR[key]}
                  strokeWidth={key === "strategy" ? 2 : 1.25}
                  strokeDasharray={
                    key === "buy_and_hold_nifty50" ? "5 3" : undefined
                  }
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div>
          <table className="data-table">
            <thead className="border-b border-slate-300 text-slate-500 dark:border-slate-700">
              <tr>
                <th>Series</th>
                <th>CAGR</th>
                <th>Volatility</th>
                <th>Sharpe</th>
                <th>Sortino</th>
                <th>Max drawdown</th>
                <th>Final value</th>
                <th>Costs</th>
                <th>Rebalances</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map(({ key, metrics }) => (
                <tr key={key}>
                  <td>
                    <span
                      aria-hidden
                      className="mr-2 inline-block h-2 w-2"
                      style={{ backgroundColor: SERIES_COLOR[key] }}
                    />
                    <span
                      className={
                        key === "strategy" ? "font-semibold" : "font-medium"
                      }
                    >
                      {SERIES_LABELS[key]}
                    </span>
                  </td>
                  <td className={`tabular ${signClass(metrics.cagr)}`}>
                    {pct(metrics.cagr)}
                  </td>
                  <td className="tabular">{pct(metrics.annualised_volatility)}</td>
                  <td className={`tabular ${signClass(metrics.sharpe_ratio)}`}>
                    {ratio(metrics.sharpe_ratio)}
                  </td>
                  <td className={`tabular ${signClass(metrics.sortino_ratio)}`}>
                    {ratio(metrics.sortino_ratio)}
                  </td>
                  <td className="tabular text-rose-700 dark:text-rose-400">
                    {pct(metrics.max_drawdown)}
                  </td>
                  <td className="tabular">{inr(metrics.end_value_inr)}</td>
                  <td className="tabular">
                    <div>{inr(metrics.total_transaction_costs_inr)}</div>
                    <div className="text-[10px] text-slate-500">
                      {pctPoints(metrics.total_transaction_costs_pct)} of capital
                    </div>
                  </td>
                  <td className="tabular">{metrics.n_rebalances}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* The verdict, and immediately beside it the caveats that undercut it. */}
        {beatIndex !== null && (
          <div className="border border-slate-300 dark:border-slate-700">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-slate-200 px-3 py-2 dark:border-slate-800">
              <h3 className="eyebrow text-slate-500">Strategy vs Nifty 50</h3>
              <p className="text-sm">
                <span
                  className={`tabular font-semibold ${
                    beatIndex
                      ? "text-emerald-700 dark:text-emerald-400"
                      : "text-rose-700 dark:text-rose-400"
                  }`}
                >
                  {beatIndex ? "+" : ""}
                  {((strategyCagr - (benchmarkCagr ?? 0)) * 100).toFixed(2)} pp
                </span>{" "}
                <span className="text-slate-600 dark:text-slate-400">
                  CAGR {beatIndex ? "above" : "below"} the index
                  {!beatIndex && " — the strategy underperformed"}
                </span>
              </p>
            </div>
            {benchmarkCaveats.length > 0 && (
              <div className="p-2">
                <Limitations
                  items={benchmarkCaveats}
                  title="Why this comparison is not evidence of skill"
                  tone="warning"
                />
              </div>
            )}
          </div>
        )}

        {bootstrap && <BootstrapPanel data={bootstrap} />}

        <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
          <DataWindowNote window={data.data_window} />
        </div>

        <Limitations items={otherCaveats} />
      </div>
    </Panel>
  );
}

const BOOTSTRAP_METRICS = [
  { key: "cagr", label: "CAGR", format: (v: number) => pct(v) },
  { key: "sharpe_ratio", label: "Sharpe", format: (v: number) => ratio(v) },
  { key: "sortino_ratio", label: "Sortino", format: (v: number) => ratio(v) },
  { key: "max_drawdown", label: "Max drawdown", format: (v: number) => pct(v) },
] as const;

function BootstrapPanel({ data }: { data: BootstrapResponse }) {
  const wins = data.win_rates;
  const benchmarkCaveats = selectBenchmarkCaveats(data.limitations);
  const otherCaveats = (data.limitations ?? []).filter(
    (item) => !benchmarkCaveats.includes(item),
  );

  return (
    <div className="border border-slate-300 dark:border-slate-700">
      <header className="border-b border-slate-200 px-3 py-2 dark:border-slate-800">
        <h3 className="eyebrow text-slate-500">
          Across {data.n_windows} {data.window_years}-year windows
        </h3>
        <p className="mt-0.5 text-[11px] text-slate-500">
          {data.method === "rolling_windows"
            ? "Rolling windows, stepped one month at a time"
            : "Stationary block bootstrap"}
          {data.total_optimizer_failures > 0 && (
            <> · {data.total_optimizer_failures} optimiser failures</>
          )}
        </p>
      </header>

      <div className="p-3">
        <table className="data-table">
          <thead className="border-b border-slate-300 text-slate-500 dark:border-slate-700">
            <tr>
              <th>Series</th>
              {BOOTSTRAP_METRICS.map((metric) => (
                <th key={metric.key} colSpan={3} className="border-l border-slate-200 text-center dark:border-slate-800">
                  {metric.label}
                </th>
              ))}
            </tr>
            <tr className="text-[10px]">
              <th />
              {BOOTSTRAP_METRICS.map((metric) => [
                <th key={`${metric.key}-p5`} className="border-l border-slate-200 font-normal dark:border-slate-800">
                  p5
                </th>,
                <th key={`${metric.key}-med`} className="font-normal">
                  median
                </th>,
                <th key={`${metric.key}-p95`} className="font-normal">
                  p95
                </th>,
              ])}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {SERIES_KEYS.filter((key) => data.summaries[key]).map((key) => (
              <tr key={key}>
                <td>
                  <span
                    aria-hidden
                    className="mr-2 inline-block h-2 w-2"
                    style={{ backgroundColor: SERIES_COLOR[key] }}
                  />
                  <span className={key === "strategy" ? "font-semibold" : ""}>
                    {SERIES_LABELS[key]}
                  </span>
                </td>
                {BOOTSTRAP_METRICS.map((metric) => {
                  const summary = data.summaries[key]?.[metric.key];
                  return [
                    <td key={`${metric.key}-p5`} className="tabular border-l border-slate-200 text-slate-500 dark:border-slate-800">
                      {summary ? metric.format(summary.p5) : "—"}
                    </td>,
                    <td key={`${metric.key}-med`} className="tabular font-medium">
                      {summary ? metric.format(summary.median) : "—"}
                    </td>,
                    <td key={`${metric.key}-p95`} className="tabular text-slate-500">
                      {summary ? metric.format(summary.p95) : "—"}
                    </td>,
                  ];
                })}
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-3 border border-slate-300 dark:border-slate-700">
          <div className="border-b border-slate-200 px-3 py-2 dark:border-slate-800">
            <h4 className="eyebrow text-slate-500">
              Win rate, paired window by window
            </h4>
            <div className="mt-1.5 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <WinRate label="vs Nifty 50 — CAGR" value={wins.vs_nifty50_cagr} />
              <WinRate label="vs Nifty 50 — Sharpe" value={wins.vs_nifty50_sharpe} />
              <WinRate label="vs hold — CAGR" value={wins.vs_buy_and_hold_cagr} />
              <WinRate label="vs hold — Sharpe" value={wins.vs_buy_and_hold_sharpe} />
            </div>
          </div>
          {benchmarkCaveats.length > 0 && (
            <div className="p-2">
              <Limitations
                items={benchmarkCaveats}
                title="Why the win rate against the index overstates the strategy"
                tone="warning"
              />
            </div>
          )}
        </div>

        <div className="mt-3">
          <DataWindowNote window={data.data_window} />
        </div>
        <div className="mt-3">
          <Limitations items={otherCaveats} />
        </div>
      </div>
    </div>
  );
}

function WinRate({ label, value }: { label: string; value: number }) {
  const percentage = value * 100;
  const tone =
    percentage >= 60
      ? "text-emerald-700 dark:text-emerald-400"
      : percentage <= 40
        ? "text-rose-700 dark:text-rose-400"
        : "text-slate-700 dark:text-slate-300";
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`tabular text-lg ${tone}`}>{percentage.toFixed(0)}%</div>
    </div>
  );
}
