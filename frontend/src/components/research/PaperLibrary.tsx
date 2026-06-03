"use client";

import { useCallback, useEffect, useState } from "react";
import { BookOpen, Calendar, FileText, MessageSquareText, RefreshCw } from "lucide-react";

import type { PaperMetadata } from "@/types/api";

const PAPERS_URL = "http://localhost:8000/api/v1/research/papers";

interface PaperLibraryProps {
  selectedPaperId?: string | null;
  refreshSignal?: number;
  onSelectPaper?: (paper: PaperMetadata) => void;
  className?: string;
}

function getPaperTitle(paper: PaperMetadata) {
  return paper.title?.trim() || paper.file_name || "Untitled paper";
}

function getPageCount(paper: PaperMetadata) {
  return paper.total_pages ?? paper.page_count ?? 0;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown date";
  }

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function normalizePaper(value: unknown): PaperMetadata | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const paper = value as Partial<PaperMetadata> & { paper_id?: string };
  const id = paper.id ?? paper.paper_id;

  if (!id || !paper.file_name || !paper.uploaded_at) {
    return null;
  }

  return {
    id,
    title: paper.title ?? null,
    file_name: paper.file_name,
    file_path: paper.file_path,
    authors: paper.authors ?? [],
    abstract: paper.abstract ?? null,
    total_pages: paper.total_pages ?? paper.page_count ?? 0,
    uploaded_at: paper.uploaded_at,
  };
}

function normalizePapers(payload: unknown): PaperMetadata[] {
  if (Array.isArray(payload)) {
    return payload.map(normalizePaper).filter((paper): paper is PaperMetadata => Boolean(paper));
  }

  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    for (const key of ["papers", "items", "data", "results"]) {
      if (Array.isArray(record[key])) {
        return record[key]
          .map(normalizePaper)
          .filter((paper): paper is PaperMetadata => Boolean(paper));
      }
    }
  }

  return [];
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

export function PaperLibrary({
  selectedPaperId,
  refreshSignal = 0,
  onSelectPaper,
  className = "",
}: PaperLibraryProps) {
  const [papers, setPapers] = useState<PaperMetadata[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPapers = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(PAPERS_URL, { cache: "no-store" });
      const payload = (await response.json().catch(() => null)) as unknown;

      if (!response.ok) {
        throw new Error(getApiError(payload, "Could not load paper library."));
      }

      setPapers(normalizePapers(payload));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Could not load paper library.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchPapers();
  }, [fetchPapers, refreshSignal]);

  return (
    <section
      className={`rounded-lg border border-white/10 bg-zinc-950 p-5 shadow-2xl shadow-black/30 ${className}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-emerald-200">
            <BookOpen className="h-4 w-4" aria-hidden="true" />
            Paper Library
          </div>
          <h2 className="mt-2 text-xl font-semibold text-white">Research papers</h2>
        </div>
        <button
          type="button"
          onClick={() => void fetchPapers()}
          disabled={isLoading}
          className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-white/10 bg-white/[0.03] text-zinc-300 transition hover:bg-white/[0.07] disabled:cursor-not-allowed disabled:opacity-50"
          aria-label="Refresh papers"
          title="Refresh papers"
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} aria-hidden="true" />
        </button>
      </div>

      {error ? (
        <p className="mt-4 rounded-md border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-sm text-amber-100">
          {error}
        </p>
      ) : null}

      {isLoading ? (
        <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <div
              key={index}
              className="h-36 animate-pulse rounded-md border border-white/10 bg-white/[0.03]"
            />
          ))}
        </div>
      ) : papers.length ? (
        <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {papers.map((paper) => {
            const isSelected = paper.id === selectedPaperId;

            return (
              <article
                key={paper.id}
                className={`flex min-h-48 flex-col rounded-md border p-4 transition ${
                  isSelected
                    ? "border-cyan-300/50 bg-cyan-300/10"
                    : "border-white/10 bg-white/[0.03] hover:bg-white/[0.05]"
                }`}
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-white/10 bg-black/30 text-cyan-200">
                    <FileText className="h-5 w-5" aria-hidden="true" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="line-clamp-2 text-sm font-semibold leading-5 text-white">
                      {getPaperTitle(paper)}
                    </h3>
                    <p className="mt-1 truncate text-xs text-zinc-500">{paper.file_name}</p>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-zinc-400">
                  <div className="rounded-md border border-white/10 bg-black/20 px-3 py-2">
                    <span className="block text-zinc-600">Pages</span>
                    <span className="mt-1 block font-medium text-zinc-200">
                      {getPageCount(paper)}
                    </span>
                  </div>
                  <div className="rounded-md border border-white/10 bg-black/20 px-3 py-2">
                    <span className="flex items-center gap-1 text-zinc-600">
                      <Calendar className="h-3 w-3" aria-hidden="true" />
                      Uploaded
                    </span>
                    <span className="mt-1 block font-medium text-zinc-200">
                      {formatDate(paper.uploaded_at)}
                    </span>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => onSelectPaper?.(paper)}
                  className="mt-auto inline-flex h-10 items-center justify-center gap-2 rounded-md bg-cyan-300 px-3 text-sm font-semibold text-zinc-950 transition hover:bg-cyan-200"
                >
                  <MessageSquareText className="h-4 w-4" aria-hidden="true" />
                  Chat with Paper
                </button>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="mt-5 grid min-h-40 place-items-center rounded-md border border-dashed border-white/10 bg-black/20 p-5 text-center">
          <div>
            <p className="text-sm font-medium text-zinc-300">No papers found</p>
            <p className="mt-2 text-sm text-zinc-600">Uploaded papers will appear here.</p>
          </div>
        </div>
      )}
    </section>
  );
}
