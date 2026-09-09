/** One typed function per endpoint. Request and response types come from the schema. */
import { apiClient, request } from "./client";
import type {
  BacktestRequest,
  BacktestResponse,
  BootstrapRequest,
  BootstrapResponse,
  HealthStatus,
  OptimizeRequest,
  OptimizeResponse,
  PriceSeries,
  SecuritySummary,
  VarRequest,
  VarResponse,
} from "./types";

export function getHealth(): Promise<HealthStatus> {
  return request(() => apiClient.get<HealthStatus>("/health"));
}

export function listSecurities(): Promise<SecuritySummary[]> {
  return request(() => apiClient.get<SecuritySummary[]>("/api/securities"));
}

export function getPrices(
  ticker: string,
  params: { start?: string; end?: string } = {},
): Promise<PriceSeries> {
  return request(() =>
    apiClient.get<PriceSeries>(
      `/api/securities/${encodeURIComponent(ticker)}/prices`,
      { params },
    ),
  );
}

export function runVar(body: VarRequest): Promise<VarResponse> {
  return request(() => apiClient.post<VarResponse>("/api/risk/var", body));
}

export function optimize(body: OptimizeRequest): Promise<OptimizeResponse> {
  return request(() =>
    apiClient.post<OptimizeResponse>("/api/portfolio/optimize", body),
  );
}

export function runBacktest(body: BacktestRequest): Promise<BacktestResponse> {
  return request(() =>
    apiClient.post<BacktestResponse>("/api/backtest/run", body),
  );
}

export function runBootstrap(
  body: BootstrapRequest,
): Promise<BootstrapResponse> {
  return request(() =>
    apiClient.post<BootstrapResponse>("/api/backtest/bootstrap", body),
  );
}
