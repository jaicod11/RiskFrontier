/** Searchable multi-select over the ingested universe. */
import { useMemo, useState } from "react";
import type { SecuritySummary } from "../api/types";
import { TextInput } from "./ui";

/**
 * Whether a row is the benchmark index rather than an investable constituent.
 *
 * NOTE: an API gap. `/api/securities` does not expose the `is_benchmark`
 * column that exists on the securities table, so this leans on the sector the
 * API does return ("Index", set when the benchmark is seeded). That is
 * API-driven rather than a hardcoded "^NSEI" check, but it is a proxy —
 * exposing `is_benchmark` on SecuritySummary would make it exact.
 */
function isBenchmark(security: SecuritySummary): boolean {
  return security.sector === "Index";
}

export function TickerPicker({
  securities,
  selected,
  onToggle,
}: {
  securities: SecuritySummary[];
  selected: Set<string>;
  onToggle: (ticker: string) => void;
}) {
  const [query, setQuery] = useState("");

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const investable = securities.filter((s) => !isBenchmark(s));
    if (!needle) return investable;
    return investable.filter(
      (s) =>
        s.ticker.toLowerCase().includes(needle) ||
        s.name.toLowerCase().includes(needle) ||
        (s.sector ?? "").toLowerCase().includes(needle),
    );
  }, [securities, query]);

  return (
    <div>
      <TextInput
        value={query}
        onChange={setQuery}
        placeholder="Search ticker, name or sector…"
      />
      <p className="mt-1 text-[11px] text-slate-500">
        {matches.length} of {securities.filter((s) => !isBenchmark(s)).length}{" "}
        · {selected.size} selected
      </p>

      <ul className="mt-2 max-h-72 divide-y divide-slate-200 overflow-y-auto border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
        {matches.map((security) => {
          const isSelected = selected.has(security.ticker);
          return (
            <li key={security.ticker}>
              <button
                type="button"
                onClick={() => onToggle(security.ticker)}
                className={`flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left text-xs transition ${
                  isSelected
                    ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                    : "hover:bg-slate-100 dark:hover:bg-slate-800"
                }`}
              >
                <span
                  aria-hidden
                  className={`inline-block h-3 w-3 shrink-0 border ${
                    isSelected
                      ? "border-white bg-white dark:border-slate-900 dark:bg-slate-900"
                      : "border-slate-400"
                  }`}
                />
                <span className="tabular w-24 shrink-0 font-medium">
                  {security.ticker}
                </span>
                <span className="truncate">{security.name}</span>
                <span
                  className={`ml-auto shrink-0 text-[10px] ${
                    isSelected ? "opacity-70" : "text-slate-500"
                  }`}
                >
                  {security.sector}
                </span>
              </button>
            </li>
          );
        })}
        {matches.length === 0 && (
          <li className="px-2.5 py-4 text-center text-xs text-slate-500">
            No securities match “{query}”.
          </li>
        )}
      </ul>
    </div>
  );
}
