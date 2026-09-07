import axios from "axios";

/**
 * Base URL of the FastAPI backend. Configured per-environment through
 * VITE_API_BASE_URL (see .env.example); falls back to the local dev backend.
 */
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10_000,
  headers: { "Content-Type": "application/json" },
});
