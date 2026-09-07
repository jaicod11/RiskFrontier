import { HealthStatus } from "../components/HealthStatus";

export default function PortfolioBuilder() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio Builder</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          Ticker selection and weight entry will live here.
        </p>
      </div>

      <HealthStatus />
    </div>
  );
}
