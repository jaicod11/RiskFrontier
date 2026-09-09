/**
 * Renders the `limitations` array a response returned.
 *
 * Deliberately always visible and inline: no accordion, no modal, no tooltip,
 * no "learn more". These caveats are large enough to reverse a conclusion, and
 * a caveat the reader has to click for is a caveat most readers never see.
 *
 * The text is never hardcoded here — it is whatever the API sent, so a change
 * to the backend's wording propagates without a frontend release.
 */

export function Limitations({
  items,
  title = "Limitations",
  tone = "neutral",
}: {
  /**
   * Optional in the generated types only because Pydantic's `default_factory`
   * emits no `default` into the OpenAPI schema. It is always sent in practice.
   */
  items: string[] | undefined;
  title?: string;
  tone?: "neutral" | "warning";
}) {
  // Rendering these is a hard requirement, so their absence is a defect worth
  // showing rather than an empty space nobody notices.
  if (!items || items.length === 0) {
    return (
      <aside className="border-l-2 border-rose-400 bg-rose-50 px-3 py-2 dark:border-rose-700 dark:bg-rose-950/40">
        <p className="text-xs text-rose-800 dark:text-rose-300">
          The API returned no limitations for this result. That is unexpected —
          every analytical response should carry them. Treat these numbers with
          extra caution and report the gap.
        </p>
      </aside>
    );
  }

  const palette =
    tone === "warning"
      ? "border-amber-400 bg-amber-50 dark:border-amber-700/70 dark:bg-amber-950/40"
      : "border-slate-300 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/60";
  const heading =
    tone === "warning"
      ? "text-amber-900 dark:text-amber-300"
      : "text-slate-600 dark:text-slate-400";
  const body =
    tone === "warning"
      ? "text-amber-900/90 dark:text-amber-100/80"
      : "text-slate-700 dark:text-slate-300";

  return (
    <aside className={`border-l-2 px-3 py-2.5 ${palette}`}>
      <h3 className={`eyebrow ${heading}`}>{title}</h3>
      <ul className="mt-1.5 space-y-1.5">
        {items.map((item) => (
          <li
            key={item}
            className={`text-xs leading-relaxed ${body}`}
          >
            {item}
          </li>
        ))}
      </ul>
    </aside>
  );
}

/**
 * The subset of a response's limitations that undercut a specific comparison.
 *
 * Matched by content rather than index so that reordering or adding backend
 * caveats cannot silently drop one from the place it matters most. Anything
 * that mentions the index comparison belongs beside that comparison.
 */
export function selectBenchmarkCaveats(items: string[] | undefined): string[] {
  if (!items) return [];
  const markers = [
    "survivor",
    "left the index",
    "price* index",
    "price index",
    "dividend",
  ];
  return items.filter((item) => {
    const lower = item.toLowerCase();
    return markers.some((marker) => lower.includes(marker.toLowerCase()));
  });
}
