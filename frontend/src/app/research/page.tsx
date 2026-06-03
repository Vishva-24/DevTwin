"use client";

import { useState } from "react";
import Link from "next/link";
import { BrainCircuit } from "lucide-react";

import { PaperLibrary } from "@/components/research/PaperLibrary";
import { PdfUpload } from "@/components/research/PdfUpload";
import { ResearchChat } from "@/components/research/ResearchChat";
import type { PaperMetadata, ResearchManifest } from "@/types/api";

export default function ResearchPage() {
  const [selectedPaper, setSelectedPaper] = useState<PaperMetadata | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [latestManifest, setLatestManifest] = useState<ResearchManifest | null>(null);

  function handlePipelineComplete(paper: PaperMetadata, manifest: ResearchManifest) {
    setSelectedPaper(paper);
    setLatestManifest(manifest);
    setRefreshSignal((value) => value + 1);
  }

  function handleSelectPaper(paper: PaperMetadata) {
    setSelectedPaper(paper);
    setLatestManifest(null);
  }

  return (
    <main className="min-h-screen bg-zinc-950">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-cyan-200">
              <BrainCircuit className="h-4 w-4" aria-hidden="true" />
              DevTwin Research Brain
            </p>
            <h1 className="mt-2 text-2xl font-semibold text-white md:text-3xl">
              Research Intelligence
            </h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Repository Intelligence
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

        <PdfUpload onPipelineComplete={handlePipelineComplete} />

        {latestManifest ? (
          <section className="rounded-lg border border-emerald-300/20 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-100">
            Manifest generated for {latestManifest.title || selectedPaper?.file_name || "paper"}.
          </section>
        ) : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(420px,0.95fr)]">
          <PaperLibrary
            selectedPaperId={selectedPaper?.id}
            refreshSignal={refreshSignal}
            onSelectPaper={handleSelectPaper}
          />
          <ResearchChat paper={selectedPaper} />
        </div>
      </div>
    </main>
  );
}
