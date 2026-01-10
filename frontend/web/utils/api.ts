// web/utils/api.ts
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

/**
 * Detects whether the app should use cookie-based sessions instead of bearer tokens.
 *
 * Inputs: none (reads NEXT_PUBLIC_USE_COOKIE_SESSION env var).
 * Outputs: true when cookie sessions are enabled, otherwise false.
 * Edge cases: treats missing/empty env var as false; value comparison is case-insensitive.
 * Concurrency/atomicity: pure read of environment variables; no shared mutable state.
 */
export function isCookieMode(): boolean {
  return (process.env.NEXT_PUBLIC_USE_COOKIE_SESSION || "").toLowerCase() === "true";
}

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

const AUTH_ERROR_MESSAGES: Record<string, string> = {
  missing_key: "Enter your access key to continue.",
  invalid_key: "That access key doesn't look right. Check it and try again.",
  key_expired: "That access key has expired. Request a new one and try again.",
  key_revoked: "That access key was revoked. Request a new one and try again.",
};

/**
 * Attempts to parse an error response body and extract a `detail` field.
 *
 * Inputs: raw response text.
 * Outputs: the `detail` string when present; otherwise null.
 * Edge cases: returns null for empty text, invalid JSON, or non-string `detail`.
 * Concurrency/atomicity: pure function; no shared state.
 */
function extractErrorDetail(text: string): string | null {
  if (!text) return null;
  try {
    const data = JSON.parse(text);
    if (data && typeof data === "object" && typeof data.detail === "string") {
      return data.detail;
    }
  } catch {
    // Non-JSON error body.
  }
  return null;
}

/**
 * Produces a user-friendly authentication error message from HTTP status and body.
 *
 * Inputs: HTTP status code and raw response text.
 * Outputs: a localized-ish message suitable for UI display.
 * Edge cases: favors known `detail` codes; falls back to status-based messaging.
 * Concurrency/atomicity: pure function; no shared state.
 */
function formatAuthError(status: number, text: string): string {
  const detail = extractErrorDetail(text);
  if (detail && AUTH_ERROR_MESSAGES[detail]) {
    return AUTH_ERROR_MESSAGES[detail];
  }
  if (status === 401) {
    return "We couldn't verify that access key. Please try again.";
  }
  return "Unable to verify the access key right now. Please try again.";
}

// Optional: snake_case -> camelCase mapper (safe no-op for primitives)
/**
 * Recursively converts object keys from snake_case to camelCase.
 *
 * Inputs: any value; arrays and plain objects are traversed.
 * Outputs: a deep-mapped structure with keys converted; primitives are returned as-is.
 * Edge cases: does not distinguish special object types; non-plain objects are treated
 *   as key/value maps via Object.entries.
 * Concurrency/atomicity: pure transformation; no shared state.
 */
export function toCamel(o: any): any {
  if (Array.isArray(o)) return o.map(toCamel);
  if (o && typeof o === "object") {
    return Object.fromEntries(
      Object.entries(o).map(([k, v]) => [
        k.replace(/_([a-z])/g, (_, c) => c.toUpperCase()),
        toCamel(v),
      ])
    );
  }
  return o;
}

/**
 * Retrieves the bearer session token from localStorage when token mode is active.
 *
 * Inputs: none (reads window.localStorage).
 * Outputs: the token string or null when unavailable.
 * Edge cases: returns null during SSR or when cookie mode is enabled.
 * Concurrency/atomicity: read-only access to localStorage; no mutation.
 */
export function getSessionToken(): string | null {
  if (isCookieMode() || typeof window === "undefined") return null;
  return window.localStorage.getItem("sessionToken");
}

/**
 * Checks backend health with a bounded timeout.
 *
 * Inputs: optional timeout in milliseconds (default 800).
 * Outputs: true if the /health endpoint responds with 2xx; false on error or timeout.
 * Edge cases: treats network errors, aborts, and non-OK responses as unhealthy.
 * Concurrency/atomicity: creates a per-call AbortController; no shared state.
 */
export async function getHealth(timeoutMs = 800): Promise<boolean> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(`${API_URL}/health`, {
      cache: "no-store",
      signal: ctrl.signal,
    });
    return r.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

export type ChatRequest = {
  question: string;
  role?: string | null;
  year?: number | null;
  tech?: string[] | null;
};

export type ChatResponse = {
  answer: string;
  citations?: Array<{ id: string; title?: string; url?: string }>;
  usage?: { input_tokens?: number; output_tokens?: number };
  input_token_limit?: number;
};

export type KeyLoginResponse = {
  access_token: string;
  token_type: string;
  expires_at: string;
};

/**
 * Builds the Authorization header for bearer-token mode.
 *
 * Inputs: none (reads session token).
 * Outputs: an object containing Authorization when available; otherwise empty object.
 * Edge cases: returns empty object in cookie mode or when no token is stored.
 * Concurrency/atomicity: read-only access to localStorage; no mutation.
 */
function authHeader(): Record<string, string> {
  if (isCookieMode()) return {};
  const token = getSessionToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Applies cookie credentials to a fetch init only when cookie mode is enabled.
 *
 * Inputs: a RequestInit object.
 * Outputs: a RequestInit that includes credentials when needed; otherwise unchanged.
 * Edge cases: preserves provided init fields and avoids mutating the input object.
 * Concurrency/atomicity: pure transformation; no shared state.
 */
function withOptionalCredentials(init: RequestInit): RequestInit {
  return isCookieMode() ? { ...init, credentials: "include" as const } : init;
}

/**
 * Exchanges an access key for a session token or cookie-based session.
 *
 * Inputs: access key string.
 * Outputs: KeyLoginResponse containing token metadata when in token mode.
 * Edge cases: on non-OK responses, attempts to map known auth errors; throws ApiError.
 * Concurrency/atomicity: each call is independent; no shared mutable state.
 */
export async function keyLogin(key: string): Promise<KeyLoginResponse> {
  const r = await fetch(`${API_URL}/auth/key-login`, withOptionalCredentials({
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ key }),
  }));
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new ApiError(formatAuthError(r.status, text), r.status);
  }
  return (await r.json()) as KeyLoginResponse;
}

/**
 * Sends a chat request to the backend and returns the model response.
 *
 * Inputs: ChatRequest payload containing question and optional filters.
 * Outputs: ChatResponse with answer, optional citations, and usage metadata.
 * Edge cases: includes Authorization header only in token mode; throws ApiError on
 *   non-OK responses with a truncated response body for context.
 * Concurrency/atomicity: each call is independent; no shared mutable state.
 */
export async function postChat(payload: ChatRequest): Promise<ChatResponse> {
  const r = await fetch(`${API_URL}/chat`, withOptionalCredentials({
    method: "POST",
    headers: { "content-type": "application/json", ...authHeader() },
    body: JSON.stringify(payload),
  }));
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new ApiError(
      `Backend error ${r.status}${text ? `: ${text.slice(0, 160)}` : ""}`,
      r.status
    );
  }
  return (await r.json()) as ChatResponse;
}

/**
 * Attempts to log out a cookie-based session.
 *
 * Inputs: none.
 * Outputs: resolves when the request completes or is skipped; no return value.
 * Edge cases: no-ops in token mode; best-effort in cookie mode and swallows failures.
 * Concurrency/atomicity: each call is independent; server-side logout is not guaranteed.
 */
export async function logout(): Promise<void> {
  if (!isCookieMode()) return;
  try {
    await fetch(`${API_URL}/auth/logout`, withOptionalCredentials({
      method: "POST",
    }));
  } catch {
    // Best-effort logout; cookie will expire naturally if this fails.
  }
}
