/** Error, loading and empty states. */
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import { Button } from "./ui";

/**
 * Shows the backend's own message. It already names the offending ticker or
 * value, which is far more useful than anything generic we could substitute.
 */
export function ErrorBanner({
  error,
  onDismiss,
}: {
  error: ApiError;
  onDismiss?: () => void;
}) {
  const detailEntries = Object.entries(error.details).filter(
    ([key]) => key !== "fields",
  );

  return (
    <div
      role="alert"
      className="border-l-2 border-rose-500 bg-rose-50 px-3 py-2.5 dark:border-rose-600 dark:bg-rose-950/40"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="eyebrow text-rose-800 dark:text-rose-300">
          {error.errorCode.replace(/_/g, " ")}
        </h3>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-xs text-rose-700 underline dark:text-rose-400"
          >
            dismiss
          </button>
        )}
      </div>
      <p className="mt-1 text-sm leading-relaxed text-rose-900 dark:text-rose-100">
        {error.message}
      </p>
      {detailEntries.length > 0 && (
        <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-rose-800/80 dark:text-rose-200/70">
          {detailEntries.map(([key, value]) => (
            <div key={key} className="flex gap-1">
              <dt className="font-medium">{key}:</dt>
              <dd className="tabular">
                {typeof value === "object" ? JSON.stringify(value) : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-1.5 text-[11px] text-rose-800/70 dark:text-rose-200/60">
        Your inputs are unchanged — adjust and run again.
      </p>
    </div>
  );
}

/**
 * A progress note rather than a bare spinner: bootstrap legitimately runs for
 * tens of seconds, and a spinner with no explanation reads as a hang.
 */
export function RunningNotice({
  label,
  detail,
  elapsedSeconds,
}: {
  label: string;
  detail?: string;
  elapsedSeconds?: number;
}) {
  return (
    <div
      role="status"
      className="flex items-start gap-3 border-l-2 border-slate-400 bg-slate-50 px-3 py-2.5 dark:border-slate-500 dark:bg-slate-900/60"
    >
      <span className="mt-1 inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-slate-500 dark:bg-slate-400" />
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-800 dark:text-slate-200">
          {label}
          {elapsedSeconds !== undefined && (
            <span className="tabular ml-2 text-xs font-normal text-slate-500">
              {elapsedSeconds}s
            </span>
          )}
        </p>
        {detail && (
          <p className="mt-0.5 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            {detail}
          </p>
        )}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="border border-dashed border-slate-300 px-5 py-8 text-center dark:border-slate-700">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
        {title}
      </h3>
      <div className="mx-auto mt-1.5 max-w-prose text-xs leading-relaxed text-slate-600 dark:text-slate-400">
        {children}
      </div>
      {action && (
        <div className="mt-3">
          <Button variant="primary" onClick={action.onClick}>
            {action.label}
          </Button>
        </div>
      )}
    </div>
  );
}
