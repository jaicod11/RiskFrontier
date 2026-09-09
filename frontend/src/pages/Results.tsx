import { useNavigate } from "react-router-dom";
import { ColdStartBanner } from "../components/ColdStart";
import { BacktestSection } from "../components/results/BacktestSection";
import { OptimizerSection } from "../components/results/OptimizerSection";
import { RiskSection } from "../components/results/RiskSection";
import { EmptyState, ErrorBanner, RunningNotice } from "../components/states";
import { Button } from "../components/ui";
import { RUN_DETAIL, useRuns } from "../hooks/useRuns";
import { usePortfolio } from "../store/portfolio";

export default function Results() {
  const navigate = useNavigate();
  const { results, holdings, weightsAreValid, clearResults } = usePortfolio();
  const runs = useRuns();

  const hasAny =
    results.var || results.optimize || results.backtest || results.bootstrap;
  const busy = runs.running !== null;

  if (!hasAny && !busy) {
    return (
      <div className="space-y-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight">Results</h1>
        </header>
        {runs.error && <ErrorBanner error={runs.error} onDismiss={runs.clearError} />}
        <EmptyState
          title="Nothing has been run yet"
          action={{ label: "Go to the portfolio builder", onClick: () => navigate("/") }}
        >
          {holdings.length === 0 ? (
            <p>
              Build a portfolio first: pick tickers, set weights totalling 100%,
              then run a risk analysis, an optimisation or a backtest. Results
              appear here.
            </p>
          ) : (
            <p>
              You have {holdings.length} holding
              {holdings.length === 1 ? "" : "s"} selected
              {weightsAreValid ? "" : ", but the weights do not total 100%"}. Run
              an analysis from the builder and the output will land here.
            </p>
          )}
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Results</h1>
          <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
            {holdings.length} holding{holdings.length === 1 ? "" : "s"} ·
            caveats are rendered with the numbers they qualify
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => navigate("/")}>← Edit portfolio</Button>
          {hasAny && (
            <Button variant="ghost" onClick={clearResults}>
              Clear results
            </Button>
          )}
        </div>
      </header>

      {runs.error && <ErrorBanner error={runs.error} onDismiss={runs.clearError} />}
      {busy &&
        (runs.isColdStart ? (
          <ColdStartBanner
            elapsedSeconds={runs.elapsedSeconds}
            context="request"
          />
        ) : (
          <RunningNotice
            label={runs.stage}
            detail={RUN_DETAIL[runs.running!]}
            elapsedSeconds={runs.elapsedSeconds}
          />
        ))}

      {results.var && <RiskSection data={results.var} />}
      {results.optimize && <OptimizerSection data={results.optimize} />}
      {results.backtest && (
        <BacktestSection data={results.backtest} bootstrap={results.bootstrap} />
      )}

      {!results.var && (
        <MissingRun
          label="Risk analysis"
          onRun={() => runs.risk()}
          disabled={!weightsAreValid || busy}
        />
      )}
      {!results.optimize && (
        <MissingRun
          label="Optimiser"
          onRun={() => runs.optimize()}
          disabled={!weightsAreValid || busy}
        />
      )}
      {!results.backtest && (
        <MissingRun
          label="Backtest"
          onRun={() => runs.backtest()}
          disabled={!weightsAreValid || busy}
        />
      )}
    </div>
  );
}

function MissingRun({
  label,
  onRun,
  disabled,
}: {
  label: string;
  onRun: () => void;
  disabled: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border border-dashed border-slate-300 px-4 py-3 dark:border-slate-700">
      <p className="text-xs text-slate-600 dark:text-slate-400">
        <span className="font-medium text-slate-800 dark:text-slate-200">
          {label}
        </span>{" "}
        has not been run for this portfolio.
      </p>
      <Button onClick={onRun} disabled={disabled}>
        Run it
      </Button>
    </div>
  );
}
