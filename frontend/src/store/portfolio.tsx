/**
 * Portfolio and run state, shared across both pages.
 *
 * Held in context and mirrored to sessionStorage so navigating to /results and
 * back does not clear a half-built portfolio. No backend persistence.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type {
  BacktestResponse,
  BootstrapResponse,
  OptimizeResponse,
  VarResponse,
} from "../api/types";

export interface Holding {
  ticker: string;
  /** Percent, as typed by the user (0-100). Converted to fractions on submit. */
  weightPct: number;
}

export interface RunParams {
  totalValueInr: number;
  // Risk
  nSims: number;
  horizonDays: number;
  lookbackDays: number;
  // Optimiser
  maxWeightPerAsset: number;
  riskFreeRate: number;
  nFrontierPoints: number;
  // Backtest
  startDate: string;
  endDate: string;
  rebalanceFrequency: "monthly" | "quarterly";
  transactionCostBps: number;
  // Bootstrap
  runBootstrap: boolean;
  windowYears: number;
  bootstrapMethod: "rolling_windows" | "block_bootstrap";
}

export const DEFAULT_PARAMS: RunParams = {
  totalValueInr: 1_000_000,
  nSims: 10_000,
  horizonDays: 10,
  lookbackDays: 504,
  maxWeightPerAsset: 0.35,
  riskFreeRate: 0.065,
  nFrontierPoints: 30,
  startDate: "2018-09-10",
  endDate: "2026-09-07",
  rebalanceFrequency: "monthly",
  transactionCostBps: 15,
  runBootstrap: false,
  windowYears: 5,
  bootstrapMethod: "rolling_windows",
};

export interface Results {
  var?: VarResponse;
  optimize?: OptimizeResponse;
  backtest?: BacktestResponse;
  bootstrap?: BootstrapResponse;
}

interface Store {
  holdings: Holding[];
  setHoldings: (holdings: Holding[]) => void;
  params: RunParams;
  setParams: (update: Partial<RunParams>) => void;
  results: Results;
  setResult: <K extends keyof Results>(key: K, value: Results[K]) => void;
  clearResults: () => void;
  totalWeightPct: number;
  weightsAreValid: boolean;
}

const PortfolioContext = createContext<Store | null>(null);
const STORAGE_KEY = "riskfrontier.portfolio.v1";

interface Persisted {
  holdings: Holding[];
  params: RunParams;
}

function load(): Persisted | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Persisted) : null;
  } catch {
    return null;
  }
}

export function PortfolioProvider({ children }: { children: ReactNode }) {
  const restored = useMemo(load, []);
  const [holdings, setHoldings] = useState<Holding[]>(restored?.holdings ?? []);
  const [params, setParamsState] = useState<RunParams>({
    ...DEFAULT_PARAMS,
    ...(restored?.params ?? {}),
  });
  // Results are deliberately not persisted: a stale chart next to an edited
  // portfolio would misrepresent what the numbers describe.
  const [results, setResults] = useState<Results>({});

  useEffect(() => {
    try {
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ holdings, params } satisfies Persisted),
      );
    } catch {
      // A private window with storage disabled is not a reason to break.
    }
  }, [holdings, params]);

  const setParams = useCallback((update: Partial<RunParams>) => {
    setParamsState((current) => ({ ...current, ...update }));
  }, []);

  const setResult = useCallback(
    <K extends keyof Results>(key: K, value: Results[K]) => {
      setResults((current) => ({ ...current, [key]: value }));
    },
    [],
  );

  const clearResults = useCallback(() => setResults({}), []);

  const totalWeightPct = useMemo(
    () => holdings.reduce((sum, holding) => sum + holding.weightPct, 0),
    [holdings],
  );

  const value: Store = {
    holdings,
    setHoldings,
    params,
    setParams,
    results,
    setResult,
    clearResults,
    totalWeightPct,
    weightsAreValid:
      holdings.length > 0 && Math.abs(totalWeightPct - 100) < 0.01,
  };

  return (
    <PortfolioContext.Provider value={value}>
      {children}
    </PortfolioContext.Provider>
  );
}

export function usePortfolio(): Store {
  const store = useContext(PortfolioContext);
  if (!store) {
    throw new Error("usePortfolio must be used inside a PortfolioProvider");
  }
  return store;
}

/** Weights as the API wants them: fractions summing to 1. */
export function toWeightFractions(holdings: Holding[]): Record<string, number> {
  return Object.fromEntries(
    holdings.map((holding) => [holding.ticker, holding.weightPct / 100]),
  );
}
