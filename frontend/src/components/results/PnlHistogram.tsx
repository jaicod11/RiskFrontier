import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MethodResult, PnlDistribution } from "../../api/types";
import { inr, inrCompact } from "../../lib/format";
import { AXIS_TICK, GRID, METHOD_COLOR, TOOLTIP_STYLE } from "./chartTheme";

/**
 * The simulated P&L distribution, both methods over the shared bins the API
 * returns. Drawn as step outlines rather than grouped bars so the two overlay
 * directly — the point is the divergence in the left tail, which side-by-side
 * bars make you compare across a gap.
 */
export function PnlHistogram({
  distribution,
  parametric,
  bootstrap,
}: {
  distribution: PnlDistribution;
  parametric: MethodResult;
  bootstrap: MethodResult;
}) {
  const { bin_edges: edges } = distribution;

  const data = distribution.parametric_counts.map((count, index) => ({
    // Step areas are drawn from the bin's left edge.
    edge: edges[index],
    center: (edges[index] + edges[index + 1]) / 2,
    parametric: count,
    bootstrap: distribution.historical_bootstrap_counts[index],
  }));

  const thresholds = [
    ...parametric.estimates.map((estimate) => ({
      method: "parametric" as const,
      level: estimate.confidence_level,
      pnl: -estimate.var_inr,
    })),
    ...bootstrap.estimates.map((estimate) => ({
      method: "historical_bootstrap" as const,
      level: estimate.confidence_level,
      pnl: -estimate.var_inr,
    })),
  ];

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h3 className="eyebrow text-slate-500">
          Simulated P&amp;L distribution
        </h3>
        <span className="text-[11px] text-slate-500">
          {distribution.n_bins} shared bins · dashed lines mark VaR thresholds
        </span>
      </div>

      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 18, right: 12, bottom: 20, left: 8 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              type="number"
              dataKey="center"
              domain={[edges[0], edges[edges.length - 1]]}
              tick={AXIS_TICK}
              tickLine={false}
              tickFormatter={(value) => inrCompact(Number(value))}
              label={{
                value: "Simulated profit / loss",
                position: "insideBottom",
                offset: -12,
                style: { fontSize: 11, fill: "#94a3b8" },
              }}
            />
            <YAxis
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={52}
              tickFormatter={(value) => Number(value).toLocaleString("en-IN")}
              label={{
                value: "Simulations",
                angle: -90,
                position: "insideLeft",
                style: { fontSize: 11, fill: "#94a3b8" },
              }}
            />
            <Tooltip
              {...TOOLTIP_STYLE}
              labelFormatter={(value) => `P&L ≈ ${inr(Number(value))}`}
              formatter={(value, name) => [
                `${Number(value).toLocaleString("en-IN")} sims`,
                name === "parametric" ? "Parametric" : "Historical bootstrap",
              ]}
            />
            <Legend
              verticalAlign="top"
              align="right"
              height={20}
              wrapperStyle={{ fontSize: 11 }}
              formatter={(value) =>
                value === "parametric" ? "Parametric" : "Historical bootstrap"
              }
            />

            {/* Break-even, so the tail is read against zero rather than the axis. */}
            <ReferenceLine x={0} stroke="#94a3b8" strokeWidth={1} />

            <Area
              type="stepAfter"
              dataKey="parametric"
              stroke={METHOD_COLOR.parametric}
              fill={METHOD_COLOR.parametric}
              fillOpacity={0.28}
              strokeWidth={1.5}
              isAnimationActive={false}
            />
            <Area
              type="stepAfter"
              dataKey="bootstrap"
              stroke={METHOD_COLOR.historical_bootstrap}
              fill={METHOD_COLOR.historical_bootstrap}
              fillOpacity={0.28}
              strokeWidth={1.5}
              isAnimationActive={false}
            />

            {thresholds.map((threshold) => (
              <ReferenceLine
                key={`${threshold.method}-${threshold.level}`}
                x={threshold.pnl}
                stroke={METHOD_COLOR[threshold.method]}
                strokeDasharray="4 3"
                strokeWidth={1.25}
                label={{
                  value: `VaR ${(threshold.level * 100).toFixed(0)}%`,
                  position: threshold.method === "parametric" ? "top" : "insideTopLeft",
                  fill: METHOD_COLOR[threshold.method],
                  fontSize: 10,
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
        Both methods binned over identical edges, so the gap in the left tail is
        a real difference between the distributions rather than an artefact of
        two different binnings. Counts sum to{" "}
        <span className="tabular">
          {distribution.parametric_counts
            .reduce((sum, count) => sum + count, 0)
            .toLocaleString("en-IN")}
        </span>{" "}
        simulations per method.
      </p>
    </div>
  );
}
