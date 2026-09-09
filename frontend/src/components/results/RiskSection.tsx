import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MethodResult, VarResponse } from "../../api/types";
import { inr, inrCompact, pctPoints } from "../../lib/format";
import { DataWindowNote } from "../DataWindowNote";
import { Limitations } from "../Limitations";
import { Metric, Panel } from "../ui";
import { AXIS_TICK, GRID, METHOD_COLOR, TOOLTIP_STYLE } from "./chartTheme";

function estimateAt(method: MethodResult, level: number) {
  return method.estimates.find(
    (estimate) => Math.abs(estimate.confidence_level - level) < 1e-9,
  );
}

export function RiskSection({ data }: { data: VarResponse }) {
  const levels = data.confidence_levels;
  const methods: { key: keyof typeof METHOD_COLOR; result: MethodResult }[] = [
    { key: "parametric", result: data.parametric },
    { key: "historical_bootstrap", result: data.historical_bootstrap },
  ];

  // Grouped bars make the divergence between the two methods the visual subject.
  const chartData = levels.flatMap((level) => [
    {
      threshold: `VaR ${(level * 100).toFixed(0)}%`,
      parametric: estimateAt(data.parametric, level)?.var_inr ?? 0,
      bootstrap: estimateAt(data.historical_bootstrap, level)?.var_inr ?? 0,
    },
    {
      threshold: `CVaR ${(level * 100).toFixed(0)}%`,
      parametric: estimateAt(data.parametric, level)?.cvar_inr ?? 0,
      bootstrap: estimateAt(data.historical_bootstrap, level)?.cvar_inr ?? 0,
    },
  ]);

  const worstLevel = levels[levels.length - 1];
  const paramCvar = estimateAt(data.parametric, worstLevel)?.cvar_inr ?? 0;
  const bootCvar = estimateAt(data.historical_bootstrap, worstLevel)?.cvar_inr ?? 0;
  const gap = paramCvar > 0 ? (bootCvar / paramCvar - 1) * 100 : 0;

  return (
    <Panel
      title="Risk — Monte Carlo VaR / CVaR"
      subtitle={
        <>
          {inr(data.total_value_inr)} portfolio ·{" "}
          <span className="tabular">{data.n_sims.toLocaleString("en-IN")}</span>{" "}
          simulations · <span className="tabular">{data.horizon_days}</span>-day
          horizon
        </>
      }
    >
      <div className="space-y-4">
        <table className="data-table">
          <thead className="border-b border-slate-300 text-slate-500 dark:border-slate-700">
            <tr>
              <th rowSpan={2} className="align-bottom">
                Method
              </th>
              {levels.map((level) => (
                <th key={level} colSpan={2} className="border-l border-slate-200 text-center dark:border-slate-800">
                  {(level * 100).toFixed(0)}% confidence
                </th>
              ))}
              <th rowSpan={2} className="align-bottom border-l border-slate-200 dark:border-slate-800">
                Worst path
              </th>
            </tr>
            <tr>
              {levels.map((level) => [
                <th key={`${level}-var`} className="border-l border-slate-200 font-normal dark:border-slate-800">
                  VaR
                </th>,
                <th key={`${level}-cvar`} className="font-normal">
                  CVaR
                </th>,
              ])}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {methods.map(({ key, result }) => (
              <tr key={key}>
                <td>
                  <span
                    aria-hidden
                    className="mr-2 inline-block h-2 w-2"
                    style={{ backgroundColor: METHOD_COLOR[key] }}
                  />
                  <span className="font-medium">
                    {key === "parametric" ? "Parametric" : "Historical bootstrap"}
                  </span>
                </td>
                {levels.map((level) => {
                  const estimate = estimateAt(result, level);
                  return [
                    <td
                      key={`${level}-var`}
                      className="tabular border-l border-slate-200 dark:border-slate-800"
                    >
                      <div className="text-rose-700 dark:text-rose-400">
                        {inr(estimate?.var_inr ?? 0)}
                      </div>
                      <div className="text-[10px] text-slate-500">
                        {pctPoints(estimate?.var_pct ?? 0)}
                      </div>
                    </td>,
                    <td key={`${level}-cvar`} className="tabular">
                      <div className="text-rose-700 dark:text-rose-400">
                        {inr(estimate?.cvar_inr ?? 0)}
                      </div>
                      <div className="text-[10px] text-slate-500">
                        {pctPoints(estimate?.cvar_pct ?? 0)}
                      </div>
                    </td>,
                  ];
                })}
                <td className="tabular border-l border-slate-200 text-rose-700 dark:border-slate-800 dark:text-rose-400">
                  {inr(result.worst_simulated_pnl_inr)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <h3 className="eyebrow text-slate-500">Loss thresholds by method</h3>
            <span className="text-[11px] text-slate-500">
              Bootstrap CVaR {(worstLevel * 100).toFixed(0)}% is{" "}
              <span
                className={`tabular font-semibold ${
                  gap > 0 ? "text-rose-700 dark:text-rose-400" : "text-slate-600"
                }`}
              >
                {gap >= 0 ? "+" : ""}
                {gap.toFixed(1)}%
              </span>{" "}
              vs parametric
            </span>
          </div>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="threshold" tick={AXIS_TICK} tickLine={false} />
                <YAxis
                  tick={AXIS_TICK}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => inrCompact(Number(value))}
                  width={64}
                />
                <Tooltip
                  {...TOOLTIP_STYLE}
                  formatter={(value, name) => [inr(Number(value)), String(name)]}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar
                  dataKey="parametric"
                  name="Parametric"
                  fill={METHOD_COLOR.parametric}
                />
                <Bar
                  dataKey="bootstrap"
                  name="Historical bootstrap"
                  fill={METHOD_COLOR.historical_bootstrap}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
            Losses at each threshold, both methods side by side. A full histogram
            of the simulated P&amp;L distribution is not drawn here: the API
            returns summary statistics rather than the simulated paths or
            histogram bins, and inventing a shape from the summary would
            misrepresent the simulation.
          </p>
        </div>

        <div className="grid gap-4 border-t border-slate-200 pt-3 sm:grid-cols-3 dark:border-slate-800">
          <Metric
            label="Mean horizon return"
            value={pctPoints(data.parametric.mean_horizon_return_pct)}
            hint="Parametric"
          />
          <Metric
            label="Best simulated path"
            value={inr(data.parametric.best_simulated_pnl_inr)}
            valueClass="text-emerald-700 dark:text-emerald-400"
          />
          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500">
              Data window
            </div>
            <div className="mt-1">
              <DataWindowNote window={data.data_window} />
            </div>
          </div>
        </div>

        <Limitations items={data.limitations} />
      </div>
    </Panel>
  );
}
