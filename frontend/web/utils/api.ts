// web/utils/api.ts
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

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

// Optional: snake_case -> camelCase mapper (safe no-op for primitives)
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

export function getSessionToken(): string | null {
  if (isCookieMode() || typeof window === "undefined") return null;
  return window.localStorage.getItem("sessionToken");
}

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
};

export type KeyLoginResponse = {
  access_token: string;
  token_type: string;
  expires_at: string;
};

function authHeader(): Record<string, string> {
  if (isCookieMode()) return {};
  const token = getSessionToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function withOptionalCredentials(init: RequestInit): RequestInit {
  return isCookieMode() ? { ...init, credentials: "include" as const } : init;
}

export async function keyLogin(key: string): Promise<KeyLoginResponse> {
  const r = await fetch(`${API_URL}/auth/key-login`, withOptionalCredentials({
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ key }),
  }));
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new ApiError(
      `Backend error ${r.status}${text ? `: ${text.slice(0, 160)}` : ""}`,
      r.status
    );
  }
  return (await r.json()) as KeyLoginResponse;
}

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
