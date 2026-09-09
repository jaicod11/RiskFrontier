/** Per-ticker weights with a running total that is loud when it is wrong. */
import type { Holding } from "../store/portfolio";
import { Button } from "./ui";

export function WeightsEditor({
  holdings,
  totalPct,
  onChange,
  onRemove,
  onEqualWeight,
}: {
  holdings: Holding[];
  totalPct: number;
  onChange: (ticker: string, weightPct: number) => void;
  onRemove: (ticker: string) => void;
  onEqualWeight: () => void;
}) {
  const off = totalPct - 100;
  const balanced = Math.abs(off) < 0.01;

  if (holdings.length === 0) {
    return (
      <p className="py-6 text-center text-xs text-slate-500">
        No holdings yet. Select tickers on the left to build a portfolio.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] text-slate-500">
          {holdings.length} holding{holdings.length === 1 ? "" : "s"}
        </span>
        <Button onClick={onEqualWeight} title="Split 100% evenly">
          Equal weight
        </Button>
      </div>

      <table className="data-table">
        <thead className="border-b border-slate-300 text-slate-500 dark:border-slate-700">
          <tr>
            <th>Ticker</th>
            <th>Weight %</th>
            <th />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {holdings.map((holding) => (
            <tr key={holding.ticker}>
              <td className="tabular font-medium">{holding.ticker}</td>
              <td>
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  max="100"
                  value={holding.weightPct}
                  onChange={(event) =>
                    onChange(holding.ticker, Number(event.target.value))
                  }
                  className="tabular w-24 border border-slate-300 bg-white px-1.5 py-0.5 text-right text-xs outline-none focus:border-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:focus:border-slate-400"
                />
              </td>
              <td className="w-8">
                <button
                  type="button"
                  onClick={() => onRemove(holding.ticker)}
                  aria-label={`Remove ${holding.ticker}`}
                  className="px-1 text-slate-400 hover:text-rose-600"
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot
          className={`border-t-2 ${
            balanced
              ? "border-slate-300 dark:border-slate-700"
              : "border-amber-500"
          }`}
        >
          <tr>
            <td className="font-semibold">Total</td>
            <td
              className={`tabular font-semibold ${
                balanced
                  ? "text-slate-900 dark:text-slate-100"
                  : "text-amber-700 dark:text-amber-400"
              }`}
            >
              {totalPct.toFixed(2)}%
            </td>
            <td />
          </tr>
        </tfoot>
      </table>

      {!balanced && (
        <p className="mt-1.5 border-l-2 border-amber-500 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          Weights must total 100%. Currently{" "}
          <span className="tabular font-semibold">{totalPct.toFixed(2)}%</span>{" "}
          — {off > 0 ? "over" : "under"} by{" "}
          <span className="tabular font-semibold">
            {Math.abs(off).toFixed(2)} pts
          </span>
          .
        </p>
      )}
    </div>
  );
}
