import { useCallback, useEffect, useState } from "react";
import { fetchHealth, type HealthResponse } from "./health";

export type HealthState =
  | { kind: "loading" }
  | { kind: "loaded"; data: HealthResponse }
  | { kind: "unreachable"; message: string };

/** Polls GET /health once on mount; `refresh` re-runs it on demand. */
export function useHealth() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  const refresh = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "loaded", data: await fetchHealth() });
    } catch (error) {
      setState({
        kind: "unreachable",
        message: error instanceof Error ? error.message : "Unknown error",
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { state, refresh };
}
