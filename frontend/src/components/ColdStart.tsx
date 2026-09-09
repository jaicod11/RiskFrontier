/**
 * Cold-start messaging for the free-tier backend.
 *
 * Render suspends a free instance after 15 minutes idle; the next request then
 * waits while it boots. A bare spinner for that long reads as a broken site, so
 * past a couple of seconds we say what is happening, show a counter that
 * visibly advances, and give an honest upper bound.
 */
import { COLD_START_HINT_SECONDS } from "../api/useHealth";

// Paces the progress bar only; the copy deliberately still says "up to a
// minute", which is the honest upper bound rather than the typical case.
// Measured cold starts on this deployment were 27.2 s and 26.4 s — that is
// Neon resuming plus Render booting, since /health hits the database. 40 s
// keeps headroom above both samples while letting the bar fill about two
// thirds by the time the page is ready, instead of stalling under halfway.
const EXPECTED_WAKE_SECONDS = 40;

function WakeProgress({ elapsedSeconds }: { elapsedSeconds: number }) {
  // Approaches but never reaches 100%: claiming completion we cannot predict
  // would be worse than an honest "still going".
  const fraction = Math.min(elapsedSeconds / EXPECTED_WAKE_SECONDS, 0.97);
  return (
    <div
      className="mt-2 h-1 w-full bg-amber-200 dark:bg-amber-900/60"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={EXPECTED_WAKE_SECONDS}
      aria-valuenow={elapsedSeconds}
    >
      <div
        className="h-1 bg-amber-600 transition-[width] duration-500 ease-linear dark:bg-amber-400"
        style={{ width: `${fraction * 100}%` }}
      />
    </div>
  );
}

export function ColdStartBanner({
  elapsedSeconds,
  context = "health",
}: {
  elapsedSeconds: number;
  context?: "health" | "request";
}) {
  return (
    <div
      role="status"
      className="border-l-2 border-amber-500 bg-amber-50 px-3 py-2.5 dark:border-amber-600 dark:bg-amber-950/40"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="eyebrow text-amber-900 dark:text-amber-300">
          Backend waking from idle
        </h3>
        <span className="tabular text-xs text-amber-800 dark:text-amber-400">
          {elapsedSeconds}s
        </span>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-amber-900/90 dark:text-amber-100/80">
        {context === "health"
          ? "The API runs on a free instance that sleeps after 15 minutes without traffic. "
          : "This is the first request since the backend went idle. "}
        Starting it takes up to a minute. Nothing is stuck — this counter is
        live, and the page will continue on its own.
      </p>
      <WakeProgress elapsedSeconds={elapsedSeconds} />
    </div>
  );
}

export { COLD_START_HINT_SECONDS };
