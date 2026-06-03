"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, FileText, Loader2, Send, UserRound } from "lucide-react";

import type { PaperMetadata, ResearchChatResponse } from "@/types/api";
import type { FormEvent, KeyboardEvent } from "react";

const RESEARCH_API_BASE = "http://localhost:8000/api/v1/research";

type ChatRole = "user" | "assistant";

interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  citations?: number[];
}

interface ResearchChatProps {
  paper: PaperMetadata | null;
  className?: string;
}

function createId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }

  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function getPaperTitle(paper: PaperMetadata) {
  return paper.title?.trim() || paper.file_name || "Untitled paper";
}

function getApiError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }

  return fallback;
}

export function ResearchChat({ paper, className = "" }: ResearchChatProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = useMemo(() => {
    return Boolean(paper?.id && input.trim() && !isLoading);
  }, [input, isLoading, paper?.id]);

  useEffect(() => {
    setMessages([]);
    setInput("");
    setSelectedCitation(null);
    setError(null);
  }, [paper?.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, isLoading]);

  async function sendMessage(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    if (!paper?.id || !canSubmit) {
      return;
    }

    const query = input.trim();
    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: query,
    };

    setMessages((current) => [...current, userMessage]);
    setInput("");
    setError(null);
    setIsLoading(true);

    try {
      const response = await fetch(`${RESEARCH_API_BASE}/${paper.id}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ query }),
      });
      const payload = (await response.json().catch(() => null)) as unknown;

      if (!response.ok) {
        throw new Error(getApiError(payload, "Research chat failed."));
      }

      const chatResponse = payload as ResearchChatResponse;
      setMessages((current) => [
        ...current,
        {
          id: createId(),
          role: "assistant",
          content: chatResponse.answer,
          citations: chatResponse.citations ?? [],
        },
      ]);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Research chat failed.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  return (
    <section
      className={`flex min-h-[640px] flex-col overflow-hidden rounded-lg border border-white/10 bg-zinc-950 shadow-2xl shadow-black/30 ${className}`}
    >
      <header className="border-b border-white/10 px-5 py-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.04] text-cyan-200">
            <Bot className="h-5 w-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-cyan-200">Research Chat</p>
            <h2 className="mt-1 truncate text-lg font-semibold text-white">
              {paper ? getPaperTitle(paper) : "Select a paper"}
            </h2>
          </div>
        </div>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-5">
        {!paper ? (
          <div className="grid h-full min-h-80 place-items-center text-center">
            <div>
              <FileText className="mx-auto h-8 w-8 text-zinc-700" aria-hidden="true" />
              <p className="mt-3 text-sm font-medium text-zinc-300">No paper selected</p>
              <p className="mt-2 text-sm text-zinc-600">Choose a paper from the library.</p>
            </div>
          </div>
        ) : messages.length === 0 ? (
          <div className="grid h-full min-h-80 place-items-center text-center">
            <div>
              <Bot className="mx-auto h-8 w-8 text-cyan-300/70" aria-hidden="true" />
              <p className="mt-3 text-sm font-medium text-zinc-300">
                Ask a grounded question about this paper.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((message) => {
              const isUser = message.role === "user";

              return (
                <div
                  key={message.id}
                  className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}
                >
                  {!isUser ? (
                    <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.04] text-cyan-200">
                      <Bot className="h-4 w-4" aria-hidden="true" />
                    </div>
                  ) : null}

                  <div className={`max-w-[82%] ${isUser ? "order-first" : ""}`}>
                    <div
                      className={`rounded-lg px-4 py-3 text-sm leading-6 ${
                        isUser
                          ? "bg-cyan-300 text-zinc-950"
                          : "border border-white/10 bg-white/[0.04] text-zinc-200"
                      }`}
                    >
                      {message.content}
                    </div>

                    {!isUser && message.citations?.length ? (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {message.citations.map((citation) => (
                          <button
                            key={`${message.id}-${citation}`}
                            type="button"
                            onClick={() => setSelectedCitation(citation)}
                            className={`rounded-md border px-2.5 py-1 text-xs font-semibold transition ${
                              selectedCitation === citation
                                ? "border-emerald-300/50 bg-emerald-300/15 text-emerald-100"
                                : "border-cyan-300/30 bg-cyan-300/10 text-cyan-100 hover:bg-cyan-300/15"
                            }`}
                            aria-pressed={selectedCitation === citation}
                          >
                            [Source {citation}]
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>

                  {isUser ? (
                    <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-cyan-300 text-zinc-950">
                      <UserRound className="h-4 w-4" aria-hidden="true" />
                    </div>
                  ) : null}
                </div>
              );
            })}

            {isLoading ? (
              <div className="flex items-center gap-3 text-sm text-zinc-500">
                <Loader2 className="h-4 w-4 animate-spin text-cyan-200" aria-hidden="true" />
                Thinking
              </div>
            ) : null}
          </div>
        )}
      </div>

      {selectedCitation !== null ? (
        <div className="border-t border-white/10 px-5 py-3 text-xs text-emerald-100">
          Selected source chunk: {selectedCitation}
        </div>
      ) : null}

      {error ? (
        <div className="border-t border-red-300/20 bg-red-300/10 px-5 py-3 text-sm text-red-200">
          {error}
        </div>
      ) : null}

      <form onSubmit={sendMessage} className="border-t border-white/10 p-4">
        <div className="flex gap-3">
          <label htmlFor="research-chat-input" className="sr-only">
            Ask about the selected paper
          </label>
          <textarea
            id="research-chat-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!paper || isLoading}
            rows={2}
            placeholder={paper ? "Ask about methodology, findings, or limitations..." : ""}
            className="min-h-12 flex-1 resize-none rounded-md border border-white/10 bg-black/30 px-3 py-3 text-sm text-white outline-none placeholder:text-zinc-600 focus:border-cyan-300/50 disabled:cursor-not-allowed disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-cyan-300 text-zinc-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
            aria-label="Send message"
            title="Send message"
          >
            {isLoading ? (
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
            ) : (
              <Send className="h-5 w-5" aria-hidden="true" />
            )}
          </button>
        </div>
      </form>
    </section>
  );
}
