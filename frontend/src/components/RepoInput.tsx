"use client";

import { useMemo, useState } from "react";
import { ArrowRight, GitBranch, Loader2 } from "lucide-react";

import type { FormEvent } from "react";
import type { AnalyzeRepoResponse, AnalyzeResult, RepoAnalysis } from "@/types/api";

const ANALYZE_URL = "http://localhost:8000/api/v1/analyze";

interface RepoInputProps {
  onAnalysisComplete?: (analysis: AnalyzeResult) => void;
  onError?: (message: string) => void;
  className?: string;
}

function isAnalysisDetail(payload: AnalyzeResult): payload is RepoAnalysis {
  return (
    "tech_stack" in payload ||
    "summary_json" in payload ||
    "ai_summary" in payload ||
    "dependency_graph" in payload
  );
}

function getErrorDetail(payload: unknown) {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : undefined;
  }
  return undefined;
}

function normalizeGithubUrl(value: string) {
  const trimmed = value.trim();
  return trimmed.endsWith("/") ? trimmed.slice(0, -1) : trimmed;
}

export function RepoInput({ onAnalysisComplete, onError, className = "" }: RepoInputProps) {
  const [githubUrl, setGithubUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = useMemo(() => {
    return /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?\/?$/.test(
      githubUrl.trim(),
    );
  }, [githubUrl]);

  async function submitRepo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const normalizedUrl = normalizeGithubUrl(githubUrl);
    if (!canSubmit) {
      const message = "Paste a valid GitHub repository URL.";
      setError(message);
      onError?.(message);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(ANALYZE_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ github_url: normalizedUrl }),
      });

      const payload = (await response.json()) as AnalyzeResult | { detail?: string };

      if (!response.ok) {
        throw new Error(getErrorDetail(payload) ?? "Repository analysis failed.");
      }

      if (isAnalysisDetail(payload as AnalyzeResult)) {
        onAnalysisComplete?.(payload as RepoAnalysis);
        return;
      }

      const submission = payload as AnalyzeRepoResponse;
      const detailResponse = await fetch(
        `http://localhost:8000/api/v1/analyses/${submission.analysis_id}`,
      );
      const detailPayload = (await detailResponse.json()) as RepoAnalysis | { detail?: string };

      if (!detailResponse.ok) {
        throw new Error(getErrorDetail(detailPayload) ?? submission.message);
      }

      onAnalysisComplete?.(detailPayload as RepoAnalysis);
    } catch (caught) {
      const message =
        caught instanceof Error ? caught.message : "Something went wrong while analyzing the repo.";
      setError(message);
      onError?.(message);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <form
      onSubmit={submitRepo}
      className={`relative overflow-hidden rounded-lg border border-white/10 bg-zinc-950 p-2 shadow-2xl shadow-black/40 ${className}`}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/50 to-transparent" />

      <div className="relative flex flex-col gap-3 rounded-md border border-white/10 bg-black/40 p-3 backdrop-blur md:flex-row md:items-center">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.06] text-cyan-200">
          <GitBranch className="h-5 w-5" aria-hidden="true" />
        </div>

        <label htmlFor="github-url" className="sr-only">
          GitHub repository URL
        </label>
        <input
          id="github-url"
          type="url"
          value={githubUrl}
          onChange={(event) => setGithubUrl(event.target.value)}
          placeholder="https://github.com/owner/repository"
          disabled={isLoading}
          className="h-12 min-w-0 flex-1 bg-transparent text-base text-white outline-none placeholder:text-zinc-500 disabled:cursor-not-allowed disabled:opacity-60"
          autoComplete="off"
          spellCheck={false}
        />

        <button
          type="submit"
          disabled={!canSubmit || isLoading}
          className="inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-md bg-cyan-300 px-5 text-sm font-semibold text-zinc-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
        >
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Analyzing
            </>
          ) : (
            <>
              Analyze
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </>
          )}
        </button>
      </div>

      {error ? (
        <p className="relative mt-3 px-2 text-sm text-red-300" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
