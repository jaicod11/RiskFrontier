/** Provenance line: which data actually backed the numbers above it. */
import type { DataWindow } from "../api/types";
import { shortDate } from "../lib/format";

export function DataWindowNote({ window }: { window: DataWindow }) {
  return (
    <p className="text-[11px] leading-relaxed text-slate-500 dark:text-slate-500">
      <span className="tabular">
        {shortDate(window.start_date)} – {shortDate(window.end_date)}
      </span>{" "}
      · <span className="tabular">{window.trading_days.toLocaleString("en-IN")}</span>{" "}
      trading days
      {window.requested_lookback_days != null && (
        <>
          {" "}
          · requested{" "}
          <span className="tabular">{window.requested_lookback_days}</span>
        </>
      )}
      {window.shrunk && window.constraint_reason && (
        <span className="mt-0.5 block text-amber-700 dark:text-amber-400">
          Window shortened: {window.constraint_reason}
        </span>
      )}
    </p>
  );
}
