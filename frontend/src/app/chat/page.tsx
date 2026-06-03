"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  BrainCircuit,
  Code2,
  Loader2,
  MessageSquare,
  Microscope,
  Network,
  Send,
  Sparkles,
} from "lucide-react";

import { invokeAgentChat, type ChatResponse } from "@/lib/api/chat";

interface ChatTurn {
  id: string;
  query: string;
  response: ChatResponse;
}

const AGENT_THEME = {
  "Knowledge Agent": {
    icon: BrainCircuit,
    className: "border-cyan-300/20 bg-cyan-300/10 text-cyan-100",
  },
  "Research Agent": {
    icon: Microscope,
    className: "border-violet-300/20 bg-violet-300/10 text-violet-100",
  },
  "Code Agent": {
    icon: Code2,
    className: "border-emerald-300/20 bg-emerald-300/10 text-emerald-100",
  },
};

export default function ChatPage() {
  const [query, setQuery] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [latestResponse, setLatestResponse] = useState<ChatResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = query.trim().length > 0 && !loading;
  const placeholder = useMemo(
    () => "Ask DevTwin what to build, learn, debug, or research next...",
    [],
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanedQuery = query.trim();
    if (!cleanedQuery || loading) {
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await invokeAgentChat({ query: cleanedQuery });
      const turn = {
        id: crypto.randomUUID(),
        query: cleanedQuery,
        response,
      };

      setTurns((current) => [...current, turn]);
      setLatestResponse(response);
      setQuery("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to invoke DevTwin chat.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-950">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-cyan-200">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
              DevTwin Agent Console
            </p>
            <h1 className="mt-2 text-2xl font-semibold text-white md:text-3xl">
              Multi-Agent Chat
            </h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/twin"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Twin Intelligence
            </Link>
            <Link
              href="/graph"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Knowledge Graph
            </Link>
            <div className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs text-zinc-500">
              FastAPI: localhost:8000
            </div>
          </div>
        </header>

        {error ? (
          <section className="rounded-lg border border-red-300/20 bg-red-300/10 px-4 py-3 text-sm text-red-100">
            {error}
          </section>
        ) : null}

        <div className="grid min-h-[720px] gap-6 xl:grid-cols-[minmax(0,1fr)_390px]">
          <section className="flex min-h-[680px] flex-col overflow-hidden rounded-lg border border-white/10 bg-zinc-950 shadow-2xl shadow-black/30">
            <div className="flex items-center gap-3 border-b border-white/10 px-4 py-4">
              <div className="grid h-10 w-10 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10">
                <MessageSquare className="h-5 w-5 text-cyan-200" aria-hidden="true" />
              </div>
              <div>
                <p className="text-sm font-semibold text-cyan-200">Conversation</p>
                <h2 className="text-lg font-semibold text-white">Ask the digital twin</h2>
              </div>
            </div>

            <div className="flex-1 space-y-5 overflow-y-auto bg-black/20 p-4">
              {turns.length === 0 ? (
                <div className="grid h-full min-h-[420px] place-items-center rounded-lg border border-dashed border-white/10">
                  <div className="max-w-md text-center">
                    <div className="mx-auto grid h-14 w-14 place-items-center rounded-lg border border-cyan-300/20 bg-cyan-300/10">
                      <Network className="h-7 w-7 text-cyan-200" aria-hidden="true" />
                    </div>
                    <p className="mt-4 text-sm font-semibold text-zinc-200">
                      Agent graph standing by
                    </p>
                    <p className="mt-2 text-sm leading-6 text-zinc-600">
                      The response will blend personal graph memory, repository architecture,
                      and cited research context when those agents are routed.
                    </p>
                  </div>
                </div>
              ) : (
                turns.map((turn) => <ChatTurnCard key={turn.id} turn={turn} />)
              )}

              {loading ? (
                <div className="flex items-center gap-3 rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-4 py-3 text-sm text-cyan-100">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Routing through LangGraph agents
                </div>
              ) : null}
            </div>

            <form onSubmit={handleSubmit} className="border-t border-white/10 bg-zinc-950 p-4">
              <div className="flex flex-col gap-3 lg:flex-row">
                <textarea
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={placeholder}
                  rows={3}
                  className="min-h-24 flex-1 resize-none rounded-lg border border-white/10 bg-black/30 px-4 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-zinc-600 focus:border-cyan-300/40"
                />
                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-cyan-300 px-5 text-sm font-semibold text-zinc-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-50 lg:w-40"
                >
                  {loading ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Send className="h-4 w-4" aria-hidden="true" />
                  )}
                  Send
                </button>
              </div>
            </form>
          </section>

          <AgentActivityPanel response={latestResponse} loading={loading} />
        </div>
      </div>
    </main>
  );
}

function ChatTurnCard({ turn }: { turn: ChatTurn }) {
  return (
    <article className="space-y-4">
      <div className="ml-auto max-w-3xl rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-4 py-3">
        <p className="text-sm font-semibold text-cyan-100">You</p>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-zinc-100">{turn.query}</p>
      </div>

      <div className="max-w-4xl rounded-lg border border-white/10 bg-zinc-950 px-4 py-4">
        <p className="text-sm font-semibold text-zinc-200">DevTwin</p>
        <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-zinc-300">
          {turn.response.answer}
        </p>
      </div>
    </article>
  );
}

function AgentActivityPanel({
  response,
  loading,
}: {
  response: ChatResponse | null;
  loading: boolean;
}) {
  const usedAgents = response?.used_agents ?? [];
  const snippets = response?.context_snippets ?? {};

  return (
    <aside className="overflow-hidden rounded-lg border border-white/10 bg-zinc-950 shadow-2xl shadow-black/30">
      <div className="flex items-center gap-3 border-b border-white/10 px-4 py-4">
        <div className="grid h-10 w-10 place-items-center rounded-md border border-violet-300/20 bg-violet-300/10">
          <BrainCircuit className="h-5 w-5 text-violet-200" aria-hidden="true" />
        </div>
        <div>
          <p className="text-sm font-semibold text-cyan-200">Agent Activity</p>
          <h2 className="text-lg font-semibold text-white">Context trace</h2>
        </div>
      </div>

      <div className="space-y-4 p-4">
        {loading ? (
          <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-black/20 px-3 py-3 text-sm text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin text-cyan-200" aria-hidden="true" />
            Waiting for agent trace
          </div>
        ) : null}

        {!loading && !response ? (
          <div className="rounded-lg border border-dashed border-white/10 px-4 py-8 text-center">
            <p className="text-sm font-semibold text-zinc-300">No run yet</p>
            <p className="mt-2 text-sm text-zinc-600">Agent context appears after a response.</p>
          </div>
        ) : null}

        {usedAgents.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {usedAgents.map((agent) => {
              const theme = AGENT_THEME[agent as keyof typeof AGENT_THEME] ?? AGENT_THEME["Knowledge Agent"];
              const Icon = theme.icon;
              return (
                <span
                  key={agent}
                  className={`inline-flex min-h-8 items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-semibold ${theme.className}`}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  {agent}
                </span>
              );
            })}
          </div>
        ) : null}

        {response ? (
          <div className="space-y-3">
            <ContextDetails title="Graph Context" items={snippets.graph ?? []} />
            <ContextDetails title="Code Context" items={snippets.code ?? []} />
            <ContextDetails title="Research Context" items={snippets.research ?? []} />
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function ContextDetails({ title, items }: { title: string; items: string[] }) {
  return (
    <details className="group rounded-lg border border-white/10 bg-black/20">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-3 text-sm font-semibold text-zinc-200">
        <span>{title}</span>
        <span className="rounded-md border border-white/10 px-2 py-1 text-xs text-zinc-500">
          {items.length}
        </span>
      </summary>
      <div className="border-t border-white/10 px-3 py-3">
        {items.length > 0 ? (
          <div className="space-y-3">
            {items.map((item, index) => (
              <pre
                key={`${title}-${index}`}
                className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-white/10 bg-zinc-950 p-3 text-xs leading-5 text-zinc-400"
              >
                {item}
              </pre>
            ))}
          </div>
        ) : (
          <p className="text-sm text-zinc-600">None available.</p>
        )}
      </div>
    </details>
  );
}
