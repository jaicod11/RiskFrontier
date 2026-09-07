import { API_BASE_URL } from "../api/client";
import { useHealth } from "../api/useHealth";

/** Live backend + Postgres reachability, checked on mount. */
export function HealthStatus() {
  const { state, refresh } = useHealth();

  const backendOk = state.kind === "loaded";
  const dbOk = state.kind === "loaded" && state.data.db === "connected";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          System status
        </h2>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded-md border border-slate-300 px-3 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700"
        >
          Re-check
        </button>
      </div>

      {state.kind === "loading" ? (
        <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">
          Checking {API_BASE_URL}/health …
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          <StatusRow label="Backend API" ok={backendOk} detail={API_BASE_URL} />
          <StatusRow
            label="Postgres"
            ok={dbOk}
            detail={
              state.kind === "loaded"
                ? state.data.db
                : "backend unreachable — cannot determine"
            }
          />
        </ul>
      )}

      {state.kind === "unreachable" && (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
          {state.message}. Is the backend running? Try{" "}
          <code className="font-mono">docker compose up</code>.
        </p>
      )}
    </section>
  );
}

function StatusRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail: string;
}) {
  return (
    <li className="flex items-center gap-3 text-sm">
      <span
        aria-hidden
        className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${
          ok ? "bg-emerald-500" : "bg-red-500"
        }`}
      />
      <span className="font-medium text-slate-800 dark:text-slate-100">{label}</span>
      <span className={ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
        {ok ? "reachable" : "unreachable"}
      </span>
      <span className="ml-auto truncate font-mono text-xs text-slate-400">{detail}</span>
    </li>
  );
}
