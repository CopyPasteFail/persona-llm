import { useEffect, useMemo, useRef, useState } from "react";

type Citation = { id: string };
type Usage = { input_tokens: number; output_tokens: number };
interface ChatResponse {
  answer: string;
  citations?: Citation[];
  usage?: Usage;
}

const API = process.env.NEXT_PUBLIC_API_URL as string | undefined;

export default function IndexPage() {
  const [ready, setReady] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<
    Array<{ role: "user" | "assistant"; content: string; usage?: Usage }>
  >([]);

  const isLocal = useMemo(() => API?.startsWith("http://localhost"), []);
  const streamRef = useRef<HTMLDivElement>(null);

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

  async function ask() {
    if (!API) { setError("API URL is not configured"); return; }
    if (!question.trim()) return;
    setLoading(true);
    setError(null);

    setMessages(prev => [...prev, { role: "user", content: question.trim() }]);

    try {
      const body = { question: question.trim() };
      const resp = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      const data: ChatResponse = await resp.json();
      if (!data?.answer) throw new Error("Backend returned no answer");
      setMessages(prev => [...prev, {
        role: "assistant",
        content: data.answer,
        usage: data.usage,
      }]);
      setQuestion("");
    } catch (err: any) {
      setError(err?.message || "Something went wrong");
    } finally {
      setLoading(false);
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
        {isLocal && (
          <span className="rounded-full border border-zinc-700 px-2 py-1 text-[11px] text-zinc-400 leading-none">
            Local backend
          </span>
        )}
      </header>

      {/* Banner area */}
      {(!ready || error) && (
        <div className="px-4 pt-3">
          <div className="rounded-xl border border-amber-700 bg-amber-950/40 px-4 py-2 text-amber-300 text-sm">
            {error ?? "Warming up the API… usually a few seconds."}
          </div>
        </div>
      )}

      {/* Main area */}
      <div className="flex-1 flex flex-col mx-auto w-full max-w-4xl px-4 py-4 overflow-hidden">
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
            <Bubble key={i} role={m.role} usage={m.usage}>
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
      </div>
    </div>
  );
}

function Bubble({ role, children, usage }: { role: "user" | "assistant"; children: any; usage?: Usage }) {
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
