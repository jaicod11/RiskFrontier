import { useState } from "react";
import {
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import type { OptimizeResponse, OptimizedPortfolio } from "../../api/types";
import { pct, ratio } from "../../lib/format";
import { DataWindowNote } from "../DataWindowNote";
import { Limitations } from "../Limitations";
import { Panel } from "../ui";
import { AXIS_TICK, GRID, TOOLTIP_STYLE } from "./chartTheme";

const FRONTIER_COLOR = "#94a3b8";
const MIN_VAR_COLOR = "#2563eb";
const MAX_SHARPE_COLOR = "#d97706";

function WeightsList({ portfolio }: { portfolio: OptimizedPortfolio }) {
  const rows = Object.entries(portfolio.weights)
    .filter(([, weight]) => weight > 0.0005)
    .sort((a, b) => b[1] - a[1]);

  return (
    <table className="data-table">
      <thead className="border-b border-slate-300 text-slate-500 dark:border-slate-700">
        <tr>
          <th>Ticker</th>
          <th>Weight</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
        {rows.map(([ticker, weight]) => (
          <tr key={ticker}>
            <td className="tabular">{ticker}</td>
            <td className="tabular">{pct(weight, 1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function OptimizerSection({ data }: { data: OptimizeResponse }) {
  const [picked, setPicked] = useState<OptimizedPortfolio | null>(null);

  const frontier = data.efficient_frontier.map((point, index) => ({
    index,
    volatility: point.volatility * 100,
    expected_return: point.expected_return * 100,
    sharpe: point.sharpe_ratio,
    point,
  }));

  const named = (portfolio: OptimizedPortfolio) => [
    {
      volatility: portfolio.volatility * 100,
      expected_return: portfolio.expected_return * 100,
      sharpe: portfolio.sharpe_ratio,
      point: portfolio,
    },
  ];

  const shown = picked ?? data.max_sharpe_portfolio;
  const shownLabel =
    picked === null
      ? "Max-Sharpe portfolio"
      : picked === data.min_variance_portfolio
        ? "Minimum-variance portfolio"
        : "Selected frontier point";

  return (
    <Panel
      title="Optimiser — Markowitz efficient frontier"
      subtitle={
        <>
          {data.tickers.length} tickers · max weight{" "}
          <span className="tabular">{pct(data.max_weight_per_asset, 0)}</span> ·
          risk-free <span className="tabular">{pct(data.risk_free_rate, 1)}</span>
          {data.frontier_points_returned < data.frontier_points_requested && (
            <>
              {" "}
              ·{" "}
              <span className="text-amber-700 dark:text-amber-400">
                {data.frontier_points_requested - data.frontier_points_returned}{" "}
                target(s) unreachable and skipped
              </span>
            </>
          )}
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div>
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 4, right: 12, bottom: 24, left: 4 }}>
                  <CartesianGrid stroke={GRID} />
                  <XAxis
                    type="number"
                    dataKey="volatility"
                    name="Volatility"
                    tick={AXIS_TICK}
                    tickLine={false}
                    domain={["dataMin - 0.5", "dataMax + 0.5"]}
                    tickFormatter={(value) => `${Number(value).toFixed(1)}%`}
                    label={{
                      value: "Annualised volatility",
                      position: "insideBottom",
                      offset: -12,
                      style: { fontSize: 11, fill: "#94a3b8" },
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="expected_return"
                    name="Expected return"
                    tick={AXIS_TICK}
                    tickLine={false}
                    domain={["dataMin - 0.5", "dataMax + 0.5"]}
                    width={64}
                    tickFormatter={(value) => `${Number(value).toFixed(1)}%`}
                    label={{
                      value: "Expected return",
                      angle: -90,
                      position: "insideLeft",
                      style: { fontSize: 11, fill: "#94a3b8" },
                    }}
                  />
                  <ZAxis range={[36, 36]} />
                  <Tooltip
                    {...TOOLTIP_STYLE}
                    cursor={{ strokeDasharray: "3 3" }}
                    formatter={(value, name) => [
                      `${Number(value).toFixed(2)}%`,
                      String(name),
                    ]}
                  />
                  <Legend
                    verticalAlign="top"
                    align="right"
                    height={22}
                    wrapperStyle={{ fontSize: 11 }}
                  />
                  <Scatter
                    name="Frontier"
                    data={frontier}
                    fill={FRONTIER_COLOR}
                    line={{ stroke: FRONTIER_COLOR, strokeWidth: 1 }}
                    onClick={(entry) => {
                      // recharts types the click payload loosely; the row we
                      // supplied carries the portfolio on `point`.
                      const row = entry as unknown as {
                        point?: OptimizedPortfolio;
                      };
                      if (row.point) setPicked(row.point);
                    }}
                    cursor="pointer"
                  />
                  <Scatter
                    name="Min variance"
                    data={named(data.min_variance_portfolio)}
                    fill={MIN_VAR_COLOR}
                    shape="square"
                    onClick={() => setPicked(data.min_variance_portfolio)}
                    cursor="pointer"
                  />
                  <Scatter
                    name="Max Sharpe"
                    data={named(data.max_sharpe_portfolio)}
                    fill={MAX_SHARPE_COLOR}
                    shape="star"
                    onClick={() => setPicked(data.max_sharpe_portfolio)}
                    cursor="pointer"
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-1 text-[11px] text-slate-500">
              Click any point to see its weights.
            </p>
          </div>

          <div>
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h3 className="eyebrow text-slate-500">{shownLabel}</h3>
              {picked && (
                <button
                  type="button"
                  onClick={() => setPicked(null)}
                  className="text-[11px] text-slate-500 underline"
                >
                  reset
                </button>
              )}
            </div>
            <dl className="mb-2 grid grid-cols-3 gap-2 border-b border-slate-200 pb-2 text-xs dark:border-slate-800">
              <div>
                <dt className="text-[10px] uppercase text-slate-500">Return</dt>
                <dd className="tabular">{pct(shown.expected_return)}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase text-slate-500">Vol</dt>
                <dd className="tabular">{pct(shown.volatility)}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase text-slate-500">Sharpe</dt>
                <dd className="tabular">{ratio(shown.sharpe_ratio)}</dd>
              </div>
            </dl>
            <WeightsList portfolio={shown} />
          </div>
        </div>

        <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
          <DataWindowNote window={data.data_window} />
        </div>

        <Limitations items={data.limitations} />
      </div>
    </Panel>
  );
}
