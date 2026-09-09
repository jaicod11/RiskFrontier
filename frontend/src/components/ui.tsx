/** Layout primitives. Deliberately plain: hairline borders, no shadows. */
import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`border border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900 ${className}`}
    >
      {(title || actions) && (
        <header className="flex items-baseline justify-between gap-4 border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
          <div className="min-w-0">
            {title && (
              <h2 className="eyebrow text-slate-500 dark:text-slate-400">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
                {subtitle}
              </p>
            )}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-slate-700 dark:text-slate-300">
        {label}
      </span>
      {children}
      {hint && (
        <span className="mt-0.5 block text-[11px] text-slate-500 dark:text-slate-500">
          {hint}
        </span>
      )}
    </label>
  );
}

const INPUT_CLASS =
  "mt-1 w-full border border-slate-300 bg-white px-2 py-1 text-sm tabular " +
  "text-slate-900 outline-none focus:border-slate-900 " +
  "dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:focus:border-slate-400";

export function NumberInput({
  value,
  onChange,
  step = 1,
  min,
  max,
  disabled,
}: {
  value: number;
  onChange: (value: number) => void;
  step?: number;
  min?: number;
  max?: number;
  disabled?: boolean;
}) {
  return (
    <input
      type="number"
      className={INPUT_CLASS}
      value={Number.isFinite(value) ? value : ""}
      step={step}
      min={min}
      max={max}
      disabled={disabled}
      onChange={(event) => {
        const next = Number(event.target.value);
        onChange(Number.isFinite(next) ? next : 0);
      }}
    />
  );
}

export function TextInput({
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <input
      type={type}
      className={INPUT_CLASS}
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function Select<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <select
      className={INPUT_CLASS}
      value={value}
      onChange={(event) => onChange(event.target.value as T)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function Button({
  children,
  onClick,
  variant = "secondary",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 border px-3 py-1.5 " +
    "text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40";
  const variants = {
    primary:
      "border-slate-900 bg-slate-900 text-white hover:bg-slate-700 " +
      "dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white",
    secondary:
      "border-slate-300 bg-white text-slate-800 hover:bg-slate-100 " +
      "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800",
    ghost:
      "border-transparent bg-transparent text-slate-600 hover:bg-slate-100 " +
      "dark:text-slate-400 dark:hover:bg-slate-800",
  };
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${variants[variant]}`}
    >
      {children}
    </button>
  );
}

export function Metric({
  label,
  value,
  valueClass = "",
  hint,
}: {
  label: string;
  value: ReactNode;
  valueClass?: string;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-500">
        {label}
      </div>
      <div className={`tabular mt-0.5 text-lg ${valueClass}`}>{value}</div>
      {hint && (
        <div className="text-[11px] text-slate-500 dark:text-slate-500">{hint}</div>
      )}
    </div>
  );
}
