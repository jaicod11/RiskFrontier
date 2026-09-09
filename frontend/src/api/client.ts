import axios, { AxiosError } from "axios";
import type { ErrorResponse } from "./types";

/**
 * Base URL of the FastAPI backend, configured through VITE_API_BASE_URL.
 */
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  // The bootstrap endpoint legitimately runs for tens of seconds.
  timeout: 180_000,
  headers: { "Content-Type": "application/json" },
});

/**
 * A backend error, normalised.
 *
 * The API writes actionable messages that name the offending ticker or value
 * ("JIOFIN has data from 2023-08-21 …"). Those are shown to the user verbatim;
 * replacing them with generic copy would throw away the useful part.
 */
export class ApiError extends Error {
  readonly errorCode: string;
  readonly details: Record<string, unknown>;
  readonly status: number | null;

  constructor(
    message: string,
    errorCode: string,
    details: Record<string, unknown> = {},
    status: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.errorCode = errorCode;
    this.details = details;
    this.status = status;
  }

  /** True when the user can fix this by changing the form. */
  get isUserFixable(): boolean {
    return this.status !== null && this.status >= 400 && this.status < 500;
  }
}

function isErrorEnvelope(value: unknown): value is ErrorResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "error_code" in value &&
    "message" in value
  );
}

/** Turn anything axios throws into an ApiError with the backend's own message. */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;

  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<unknown>;
    const body = axiosError.response?.data;

    if (isErrorEnvelope(body)) {
      return new ApiError(
        body.message,
        body.error_code,
        (body.details ?? {}) as Record<string, unknown>,
        axiosError.response?.status ?? null,
      );
    }

    if (axiosError.code === "ECONNABORTED") {
      return new ApiError(
        "The request timed out. Bootstrap runs over many windows and can take " +
          "a while — try fewer resamples or a shorter window.",
        "TIMEOUT",
        {},
        null,
      );
    }

    if (!axiosError.response) {
      return new ApiError(
        `Could not reach the backend at ${API_BASE_URL}. Is it running?`,
        "NETWORK_ERROR",
        {},
        null,
      );
    }

    return new ApiError(
      axiosError.message,
      "HTTP_ERROR",
      {},
      axiosError.response.status,
    );
  }

  return new ApiError(
    error instanceof Error ? error.message : "Unexpected error",
    "UNKNOWN",
  );
}

/** Every endpoint call goes through here, so error handling exists once. */
export async function request<T>(fn: () => Promise<{ data: T }>): Promise<T> {
  try {
    const { data } = await fn();
    return data;
  } catch (error) {
    throw toApiError(error);
  }
}
