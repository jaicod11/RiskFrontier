/** Health as shared state, so a run can tell whether the backend is awake. */
import { createContext, useContext, type ReactNode } from "react";
import { useHealth } from "../api/useHealth";

type HealthStore = ReturnType<typeof useHealth>;

const HealthContext = createContext<HealthStore | null>(null);

export function HealthProvider({ children }: { children: ReactNode }) {
  const health = useHealth();
  return (
    <HealthContext.Provider value={health}>{children}</HealthContext.Provider>
  );
}

export function useHealthStore(): HealthStore {
  const store = useContext(HealthContext);
  if (!store) {
    throw new Error("useHealthStore must be used inside a HealthProvider");
  }
  return store;
}
