"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BrainCircuit, Loader2, Network, RefreshCcw, Target } from "lucide-react";

import { EvolutionChart } from "@/components/twin/EvolutionChart";
import { SkillGap } from "@/components/twin/SkillGap";
import { SkillRadar } from "@/components/twin/SkillRadar";
import {
  fetchSkillGap,
  fetchTwinProfile,
  fetchTwinSkills,
  type SkillGapResponse,
  type SkillStrength,
  type TwinProfile,
} from "@/lib/api/twin";

export default function TwinPage() {
  const [profile, setProfile] = useState<TwinProfile | null>(null);
  const [skills, setSkills] = useState<SkillStrength[]>([]);
  const [gap, setGap] = useState<SkillGapResponse | null>(null);
  const [targetRole, setTargetRole] = useState("MLEngineer");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTwin = useCallback(async (role: string) => {
    setLoading(true);
    setError(null);

    try {
      const skillData = await fetchTwinSkills(50);
      const [profileData, gapData] = await Promise.all([fetchTwinProfile(), fetchSkillGap(role)]);

      setSkills(skillData);
      setProfile(profileData);
      setGap(gapData);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load personal twin data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTwin(targetRole);
  }, [loadTwin, targetRole]);

  function handleRefresh() {
    void loadTwin(targetRole);
  }

  return (
    <main className="min-h-screen bg-zinc-950">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-cyan-200">
              <BrainCircuit className="h-4 w-4" aria-hidden="true" />
              DevTwin Personal Learning Graph
            </p>
            <h1 className="mt-2 text-2xl font-semibold text-white md:text-3xl">
              Twin Intelligence
            </h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/graph"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Knowledge Graph
            </Link>
            <Link
              href="/research"
              className="inline-flex h-9 items-center rounded-md border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-200"
            >
              Research Intelligence
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

        {error ? (
          <section className="rounded-lg border border-red-300/20 bg-red-300/10 px-4 py-3 text-sm text-red-100">
            {error}
          </section>
        ) : null}

        <section className="grid gap-4 lg:grid-cols-4">
          <MetricCard
            icon={Network}
            label="Repositories"
            value={profile?.repository_count ?? 0}
            loading={loading}
          />
          <MetricCard
            icon={BrainCircuit}
            label="Papers"
            value={profile?.paper_count ?? 0}
            loading={loading}
          />
          <MetricCard
            icon={Target}
            label="Skills"
            value={profile?.skill_count ?? skills.length}
            loading={loading}
          />
          <MetricCard
            icon={RefreshCcw}
            label="Avg confidence"
            value={`${Math.round((profile?.average_confidence ?? 0) * 100)}%`}
            loading={loading}
          />
        </section>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(420px,0.95fr)]">
          <SkillRadar skills={skills} loading={loading} />
          <EvolutionChart />
        </div>

        <SkillGap
          gap={gap}
          loading={loading}
          targetRole={targetRole}
          onTargetRoleChange={setTargetRole}
          onRefresh={handleRefresh}
        />
      </div>
    </main>
  );
}

interface MetricCardProps {
  icon: typeof Network;
  label: string;
  value: number | string;
  loading: boolean;
}

function MetricCard({ icon: Icon, label, value, loading }: MetricCardProps) {
  return (
    <div className="rounded-lg border border-white/10 bg-zinc-950 px-4 py-4 shadow-2xl shadow-black/20">
      <div className="flex items-center justify-between gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10">
          <Icon className="h-4 w-4 text-cyan-200" aria-hidden="true" />
        </div>
        {loading ? <Loader2 className="h-4 w-4 animate-spin text-zinc-600" aria-hidden="true" /> : null}
      </div>
      <p className="mt-4 text-xs font-medium text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-white">{value}</p>
    </div>
  );
}
