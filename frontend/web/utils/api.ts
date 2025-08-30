// web/utils/api.ts
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

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

export async function postChat(payload: ChatRequest): Promise<ChatResponse> {
  const r = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(
      `Backend error ${r.status}${text ? `: ${text.slice(0, 160)}` : ""}`
    );
  }
  return (await r.json()) as ChatResponse;
}
