import { apiClient } from "./client";

export interface HealthResponse {
  status: "ok" | "error";
  db: "connected" | "disconnected";
}

/**
 * Calls GET /health. The backend answers 503 with the same shape when it
 * cannot reach Postgres, so that body is returned rather than thrown.
 */
export async function fetchHealth(): Promise<HealthResponse> {
  try {
    const { data } = await apiClient.get<HealthResponse>("/health");
    return data;
  } catch (error) {
    if (axiosHasHealthBody(error)) {
      return error.response.data;
    }
    throw error;
  }
}

function axiosHasHealthBody(
  error: unknown,
): error is { response: { data: HealthResponse } } {
  return (
    typeof error === "object" &&
    error !== null &&
    "response" in error &&
    typeof (error as { response?: { data?: unknown } }).response?.data === "object" &&
    (error as { response: { data: HealthResponse } }).response.data !== null &&
    "db" in (error as { response: { data: HealthResponse } }).response.data
  );
}
