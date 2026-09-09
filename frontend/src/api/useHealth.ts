import { useCallback, useEffect, useRef, useState } from "react";
import { fetchHealth, type HealthResponse } from "./health";

/**
 * Render's free tier suspends an instance after 15 minutes idle. The first
 * request then waits for the container to boot and for Neon to resume —
 * measured at 27.2 s and 26.4 s on this deployment. Past this many seconds we
 * stop showing a plain spinner and say what is actually happening.
 */
export const COLD_START_HINT_SECONDS = 2;

export type HealthState =
  | { kind: "loading" }
  | { kind: "loaded"; data: HealthResponse }
  | { kind: "unreachable"; message: string };

/** Polls GET /health once on mount; `refresh` re-runs it on demand. */
export function useHealth() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const startedAt = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    startedAt.current = Date.now();
    setElapsedSeconds(0);
    setState({ kind: "loading" });
    try {
      setState({ kind: "loaded", data: await fetchHealth() });
    } catch (error) {
      setState({
        kind: "unreachable",
        message: error instanceof Error ? error.message : "Unknown error",
      });
    } finally {
      startedAt.current = null;
    }
  }, []);

  // A counter that visibly advances is the difference between "waking up" and
  // "hung" for someone staring at the screen.
  useEffect(() => {
    if (state.kind !== "loading") return;
    const timer = window.setInterval(() => {
      if (startedAt.current === null) return;
      setElapsedSeconds(Math.round((Date.now() - startedAt.current) / 1000));
    }, 500);
    return () => window.clearInterval(timer);
  }, [state.kind]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return {
    state,
    refresh,
    elapsedSeconds,
    /** Health is taking long enough that the instance is probably asleep. */
    isColdStarting:
      state.kind === "loading" && elapsedSeconds >= COLD_START_HINT_SECONDS,
    isAwake: state.kind === "loaded" && state.data.db === "connected",
  };
}
