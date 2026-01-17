import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatResponse } from "../utils/api";
import { ApiError, getSessionToken, isCookieMode, keyLogin, logout, postChat } from "../utils/api";

type Usage = NonNullable<ChatResponse["usage"]>;

const API = process.env.NEXT_PUBLIC_API_URL as string | undefined;

export default function IndexPage() {
  const [ready, setReady] = useState(true);
  const [loading, setLoading] = useState(false);
  const [authLoading, setAuthLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [accessKey, setAccessKey] = useState("");
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [cookieSessionActive, setCookieSessionActive] = useState(false);
  const [messages, setMessages] = useState<
    Array<{ role: "user" | "assistant"; content: string; usage?: Usage; inputTokenLimit?: number }>
  >([]);

  const isCookieSession = isCookieMode();
  const isLocal = useMemo(() => API?.startsWith("http://localhost"), []);
  const streamRef = useRef<HTMLDivElement>(null);
  const hasSession = isCookieSession ? cookieSessionActive : Boolean(sessionToken);
  const bannerMessage = error ?? (!ready ? "Warming up the API… usually a few seconds." : null);

  useEffect(() => {
    if (isCookieSession) return;
    const existing = getSessionToken();
    if (existing) setSessionToken(existing);
  }, [isCookieSession]);

  // Auto-scroll to bottom when messages update
  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [messages, loading]);

  // Health probe
  useEffect(() => {
    if (!API) { setReady(false); return; }
    let stop = false;
    async function probe() {
      try {
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), 800);
        const r = await fetch(`${API}/health`, { signal: ctl.signal });
        clearTimeout(t);
        if (!stop) setReady(r.ok);
      } catch {
        if (!stop) setReady(false);
      }
    }
    probe();
    const id = setInterval(probe, 1200);
    return () => { stop = true; clearInterval(id); };
  }, []);

  function clearSession(message?: string) {
    if (!isCookieSession && typeof window !== "undefined") {
      window.localStorage.removeItem("sessionToken");
    }
    setSessionToken(null);
    setCookieSessionActive(false);
    setError(null);
    setMessages([]);
    setQuestion("");
    if (isCookieSession) {
      logout().catch(() => {});
    }
    if (message) setAuthError(message);
  }

  async function ask() {
    if (!API) { setError("API URL is not configured"); return; }
    if (!hasSession) { setAuthError("Enter your access key to start."); return; }
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    setAuthError(null);

    setMessages(prev => [...prev, { role: "user", content: question.trim() }]);

    try {
      const data = await postChat({ question: question.trim() });
      if (!data?.answer) throw new Error("Backend returned no answer");
      setMessages(prev => [...prev, {
        role: "assistant",
        content: data.answer,
        usage: data.usage,
        inputTokenLimit: data.input_token_limit,
      }]);
      setQuestion("");
    } catch (err: any) {
      const message = err?.message || "Something went wrong";
      setError(message);
      if (err instanceof ApiError && err.status === 401) {
        clearSession("Session expired. Enter a new access key.");
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleKeySubmit(e: any) {
    e.preventDefault();
    if (!API) { setAuthError("API URL is not configured"); return; }
    if (!accessKey.trim()) { setAuthError("Enter an access key"); return; }
    setAuthLoading(true);
    setAuthError(null);
    setError(null);
    try {
      const res = await keyLogin(accessKey.trim());
      if (isCookieSession) {
        setCookieSessionActive(true);
      } else if (typeof window !== "undefined") {
        window.localStorage.setItem("sessionToken", res.access_token);
        setSessionToken(res.access_token);
      }
      setAccessKey("");
    } catch (err: any) {
      const message = err instanceof ApiError ? err.message : "Invalid access key";
      clearSession(message);
    } finally {
      setAuthLoading(false);
    }
  }

  function buildSuggestions() {
    return [
      "Summarize your Kubernetes experience in two sentences.",
      "How would you explain your SRE approach to a non-technical recruiter?",
      "Which CI/CD choices do you usually make and why?",
    ];
  }

  return (
    <div className="h-screen flex flex-col bg-zinc-950 text-zinc-100">
      {/* Header */}
      <header className="border-b border-zinc-900/60 bg-zinc-950 px-4 py-3 flex justify-between items-center">
        <div className="flex items-center gap-3">
          <div className="h-8 w-8 rounded-xl bg-zinc-100 text-zinc-900 grid place-items-center font-semibold">P</div>
          <div>
            <div className="text-lg font-semibold">Persona</div>
            <div className="text-xs text-zinc-400">Grounded answers from your profile</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isLocal && (
            <span className="rounded-full border border-zinc-700 px-2 py-1 text-[11px] text-zinc-400 leading-none">
              Local backend
            </span>
          )}
          {hasSession && (
            <button
              type="button"
              onClick={() => clearSession()}
              className="rounded-full border border-zinc-700 px-3 py-1 text-[11px] text-zinc-200 hover:border-zinc-500"
            >
              End session
            </button>
          )}
        </div>
      </header>

      {/* Banner area */}
      {bannerMessage && (
        <div className="px-4 pt-3">
          <div className="rounded-xl border border-amber-700 bg-amber-950/40 px-4 py-2 text-amber-300 text-sm">
            {bannerMessage}
          </div>
        </div>
      )}

      {/* Main area */}
      <div className="flex-1 flex flex-col mx-auto w-full max-w-4xl px-4 py-4 overflow-hidden">
        {!hasSession ? (
          <div className="flex-1 flex items-center justify-center">
            <form
              onSubmit={handleKeySubmit}
              className="w-full max-w-md space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6 shadow-lg"
            >
              <div className="space-y-1 text-center">
                <div className="text-lg font-semibold">Enter access key</div>
                <p className="text-sm text-zinc-400">Access keys are temporary. A session lasts about an hour.</p>
              </div>
              <input
                type="password"
                value={accessKey}
                onChange={(e) => setAccessKey(e.target.value)}
                placeholder="Paste your temporary key"
                className="w-full rounded-xl bg-zinc-950/60 border border-zinc-800 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                disabled={authLoading || !ready}
              />
              {authError && (
                <div className="rounded-lg border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-300">
                  {authError}
                </div>
              )}
              <button
                type="submit"
                disabled={authLoading || !ready || !accessKey.trim()}
                className="w-full rounded-xl bg-white text-zinc-900 px-4 py-2.5 font-medium hover:bg-white/90 disabled:opacity-50"
              >
                {authLoading ? "Checking…" : "Start session"}
              </button>
              <div className="text-xs text-zinc-500 text-center">
                {isCookieSession ? "Session uses a secure HttpOnly cookie in this browser." : "Session tokens are stored locally in your browser."}
              </div>
            </form>
          </div>
        ) : (
          <>
            {/* Conversation box */}
            <div
              ref={streamRef}
              className="flex-1 overflow-y-auto rounded-2xl border border-zinc-800 bg-zinc-900/30 p-4 space-y-4"
            >
              {messages.length === 0 && (
                <div className="text-sm text-zinc-400">
                  Try a starter:
                  <div className="mt-2 flex flex-wrap gap-2">
                    {buildSuggestions().map((s) => (
                      <button
                        key={s}
                        onClick={() => { if (ready && !loading) setQuestion(s); }}
                        disabled={!ready || loading}
                        className="rounded-full border border-zinc-700/80 px-3 py-1 text-[12px] hover:bg-zinc-800/60 disabled:opacity-40"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((m, i) => (
                <Bubble key={i} role={m.role} usage={m.usage} inputTokenLimit={m.inputTokenLimit}>
                  {m.content}
                </Bubble>
              ))}
              {loading && <AssistantSkeleton />}
            </div>

            {/* Input bar */}
            <form
              onSubmit={(e) => { e.preventDefault(); if (!loading && ready) ask(); }}
              className="mt-3 border-t border-zinc-800/80 bg-zinc-950/50 p-3"
            >
              <div className="flex items-end gap-2">
                <textarea
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="Ask me anything…"
                  className="flex-1 rounded-xl bg-zinc-950/60 border border-zinc-800 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                  rows={3}
                  disabled={!ready || loading}
                />
                <button
                  type="submit"
                  disabled={loading || !ready || !question.trim()}
                  className="shrink-0 rounded-xl bg-white text-zinc-900 px-4 py-2.5 font-medium hover:bg-white/90 disabled:opacity-50"
                >
                  {loading ? "Asking…" : "Send"}
                </button>
              </div>
            </form>
          </>
        )}
      </div>
    </div>
  );
}

function Bubble({
  role,
  children,
  usage,
  inputTokenLimit,
}: {
  role: "user" | "assistant";
  children: any;
  usage?: Usage;
  inputTokenLimit?: number;
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={[
          "max-w-[85%] rounded-2xl px-4 py-3 shadow-sm",
          isUser ? "bg-zinc-100 text-zinc-900" : "bg-zinc-900/70 border border-zinc-800 text-zinc-200",
        ].join(" ")}
      >
        <div className="whitespace-pre-wrap">{children}</div>
        {usage && (
          <div className="mt-2 flex gap-2 text-[10px] text-zinc-500">
            <span>in: {usage.input_tokens}</span>
            <span>out: {usage.output_tokens}</span>
            {typeof inputTokenLimit === "number" && <span>limit: {inputTokenLimit}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function AssistantSkeleton() {
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-zinc-900/40 border border-zinc-800 text-zinc-400">
        <div className="animate-pulse space-y-2">
          <div className="h-4 w-48 bg-zinc-700/40 rounded" />
          <div className="h-4 w-80 bg-zinc-700/40 rounded" />
          <div className="h-4 w-64 bg-zinc-700/40 rounded" />
        </div>
      </div>
    </div>
  );
}
