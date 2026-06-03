"use client";

import Link from "next/link";
import { Network } from "lucide-react";

import { GraphCanvas } from "@/components/graph/GraphCanvas";

export default function GraphPage() {
  return (
    <main className="min-h-screen bg-zinc-950">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-cyan-200">
              <Network className="h-4 w-4" aria-hidden="true" />
              DevTwin Knowledge Graph
            </p>
            <h1 className="mt-2 text-2xl font-semibold text-white md:text-3xl">
              Visual Knowledge Graph
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
              href="/research"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Research Intelligence
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

        <GraphCanvas />
      </div>
    </main>
  );
}
