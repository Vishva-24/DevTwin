"use client";

import { Loader2, RefreshCcw, Target } from "lucide-react";

import type { SkillGapResponse } from "@/lib/api/twin";

interface SkillGapProps {
  gap: SkillGapResponse | null;
  loading?: boolean;
  targetRole: string;
  onTargetRoleChange: (targetRole: string) => void;
  onRefresh: () => void;
}

const TARGET_ROLES = ["MLEngineer", "AIEngineer", "BackendEngineer"];

export function SkillGap({
  gap,
  loading = false,
  targetRole,
  onTargetRoleChange,
  onRefresh,
}: SkillGapProps) {
  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-zinc-950 text-zinc-100 shadow-2xl shadow-black/30">
      <div className="flex flex-col gap-4 border-b border-white/10 px-4 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md border border-amber-300/20 bg-amber-300/10">
            <Target className="h-5 w-5 text-amber-200" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-semibold text-cyan-200">Skill Gap</p>
            <h2 className="text-lg font-semibold text-white">{targetRole}</h2>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          {TARGET_ROLES.map((role) => {
            const isActive = targetRole === role;
            return (
              <button
                key={role}
                type="button"
                onClick={() => onTargetRoleChange(role)}
                className={`h-9 rounded-md border px-3 text-xs font-semibold transition ${
                  isActive
                    ? "border-cyan-300/40 bg-cyan-300/10 text-cyan-100"
                    : "border-white/10 text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200"
                }`}
              >
                {role}
              </button>
            );
          })}
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-white/10 text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Refresh skill gap"
            title="Refresh skill gap"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-2">
        <SkillColumn
          title="You have"
          tone="green"
          emptyText="No matching strengths yet."
          items={(gap?.have ?? []).map((skill) => ({
            key: `${skill.label}-${skill.name}`,
            name: skill.name,
            detail: `${Math.round(skill.strength_score)} strength`,
          }))}
        />
        <SkillColumn
          title="You're missing"
          tone="red"
          emptyText="No missing graph skills found."
          items={(gap?.missing ?? []).map((skill) => ({
            key: `${skill.label}-${skill.name}`,
            name: skill.name,
            detail: `${skill.graph_count} graph signals`,
          }))}
        />
      </div>
    </section>
  );
}

interface SkillColumnProps {
  title: string;
  tone: "green" | "red";
  emptyText: string;
  items: Array<{
    key: string;
    name: string;
    detail: string;
  }>;
}

function SkillColumn({ title, tone, emptyText, items }: SkillColumnProps) {
  const toneClasses =
    tone === "green"
      ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100"
      : "border-red-300/20 bg-red-300/10 text-red-100";

  return (
    <div className="min-h-[240px] rounded-lg border border-white/10 bg-black/20 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <span className="rounded-md border border-white/10 px-2 py-1 text-xs text-zinc-500">
          {items.length}
        </span>
      </div>

      {items.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {items.map((item) => (
            <span
              key={item.key}
              className={`inline-flex min-h-8 max-w-full items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-semibold ${toneClasses}`}
              title={item.detail}
            >
              <span className="truncate">{item.name}</span>
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-4 text-sm text-zinc-600">{emptyText}</p>
      )}
    </div>
  );
}
