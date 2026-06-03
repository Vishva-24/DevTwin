"use client";

import { useState } from "react";
import Link from "next/link";

import { DependencyGraph } from "@/components/DependencyGraph";
import { RepoInput } from "@/components/RepoInput";
import { SummaryCard } from "@/components/SummaryCard";
import type { AnalyzeResult, RepoAnalysis } from "@/types/api";

function isRepoAnalysis(result: AnalyzeResult): result is RepoAnalysis {
  return "id" in result && ("tech_stack" in result || "summary_json" in result);
}

export default function Home() {
  const [analysis, setAnalysis] = useState<RepoAnalysis | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function handleAnalysisComplete(result: AnalyzeResult) {
    if (isRepoAnalysis(result)) {
      setAnalysis(result);
      setNotice(
        result.status === "failed"
          ? result.error_message ?? "Analysis failed before structured parser output was saved."
          : null,
      );
      return;
    }

    setNotice(result.message);
  }

  return (
    <main className="min-h-screen bg-zinc-950">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-sm font-medium text-cyan-200">DevTwin</p>
            <h1 className="mt-2 text-2xl font-semibold text-white md:text-3xl">
              Repository Intelligence
            </h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/research"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Research Intelligence
            </Link>
            <Link
              href="/graph"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Knowledge Graph
            </Link>
            <Link
              href="/twin"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Twin Intelligence
            </Link>
            <Link
              href="/chat"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Agent Chat
            </Link>
            <div className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs text-zinc-500">
              FastAPI: localhost:8000
            </div>
          </div>
        </header>

        <RepoInput onAnalysisComplete={handleAnalysisComplete} onError={setNotice} />

        {notice ? (
          <div className="rounded-md border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
            {notice}
          </div>
        ) : null}

        {analysis ? (
          <div className="grid gap-6">
            <SummaryCard analysis={analysis} />
            <DependencyGraph dependencyGraph={analysis.dependency_graph} />
          </div>
        ) : (
          <section className="grid min-h-[360px] place-items-center rounded-lg border border-dashed border-white/10 bg-black/20 p-6">
            <div className="text-center">
              <p className="text-sm font-medium text-zinc-300">Awaiting repository analysis</p>
              <p className="mt-2 text-sm text-zinc-600">Results will appear here.</p>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
