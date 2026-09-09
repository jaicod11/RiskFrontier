import { NavLink, Outlet } from "react-router-dom";
import { useHealth } from "../api/useHealth";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `border-b-2 px-1 pb-1.5 text-xs font-medium transition ${
    isActive
      ? "border-slate-900 text-slate-900 dark:border-slate-100 dark:text-slate-100"
      : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
  }`;

export function Layout() {
  const { state } = useHealth();
  const healthy = state.kind === "loaded" && state.data.db === "connected";

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-300 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-6 pt-3">
          <span className="text-sm font-semibold tracking-tight">
            RiskFrontier
            <span className="ml-2 font-normal text-slate-400">NSE</span>
          </span>
          <nav className="flex gap-4">
            <NavLink to="/" end className={linkClass}>
              Portfolio builder
            </NavLink>
            <NavLink to="/results" className={linkClass}>
              Results
            </NavLink>
          </nav>
          <span className="ml-auto flex items-center gap-1.5 pb-1.5 text-[11px] text-slate-500">
            <span
              aria-hidden
              className={`inline-block h-1.5 w-1.5 rounded-full ${
                state.kind === "loading"
                  ? "bg-slate-400"
                  : healthy
                    ? "bg-emerald-500"
                    : "bg-rose-500"
              }`}
            />
            {state.kind === "loading"
              ? "checking"
              : healthy
                ? "backend connected"
                : "backend unreachable"}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
