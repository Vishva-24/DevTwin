"use client";

import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { Activity, Loader2 } from "lucide-react";

import type { SkillStrength } from "@/lib/api/twin";

interface SkillRadarProps {
  skills: SkillStrength[];
  loading?: boolean;
}

export function SkillRadar({ skills, loading = false }: SkillRadarProps) {
  const topSkills = skills.slice(0, 8);
  const chartData = topSkills.map((skill) => ({
    skill: shortenSkillName(skill.name),
    fullName: skill.name,
    strength: Math.round(skill.strength_score),
  }));

  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-zinc-950 text-zinc-100 shadow-2xl shadow-black/30">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10">
            <Activity className="h-5 w-5 text-cyan-200" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-semibold text-cyan-200">Skill Radar</p>
            <h2 className="text-lg font-semibold text-white">Top strengths</h2>
          </div>
        </div>
        <span className="rounded-md border border-white/10 px-2 py-1 text-xs text-zinc-500">
          {topSkills.length}/8
        </span>
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="relative h-[360px] min-w-0 rounded-lg border border-white/10 bg-black/25">
          {loading ? (
            <div className="absolute inset-0 z-10 grid place-items-center bg-zinc-950/70">
              <div className="flex items-center gap-2 text-sm text-zinc-300">
                <Loader2 className="h-4 w-4 animate-spin text-cyan-200" aria-hidden="true" />
                Loading skills
              </div>
            </div>
          ) : null}

          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={chartData} outerRadius="72%">
                <PolarGrid stroke="rgba(255,255,255,0.12)" />
                <PolarAngleAxis dataKey="skill" tick={{ fill: "#d4d4d8", fontSize: 11 }} />
                <PolarRadiusAxis
                  angle={90}
                  domain={[0, 100]}
                  tick={{ fill: "#71717a", fontSize: 10 }}
                  axisLine={false}
                />
                <Radar
                  name="Strength"
                  dataKey="strength"
                  stroke="#67e8f9"
                  fill="#67e8f9"
                  fillOpacity={0.28}
                  strokeWidth={2}
                />
                <Tooltip
                  cursor={{ stroke: "#67e8f9", strokeOpacity: 0.24 }}
                  formatter={(value, name) => [`${value}`, name]}
                  labelFormatter={(_, payload) => {
                    const item = payload?.[0]?.payload as { fullName?: string } | undefined;
                    return item?.fullName ?? "";
                  }}
                  contentStyle={{
                    background: "#09090b",
                    border: "1px solid rgba(255,255,255,0.12)",
                    borderRadius: 8,
                    color: "#f4f4f5",
                  }}
                />
              </RadarChart>
            </ResponsiveContainer>
          ) : (
            <div className="grid h-full place-items-center px-6 text-center">
              <div>
                <p className="text-sm font-semibold text-zinc-200">No skills inferred yet</p>
                <p className="mt-2 text-sm text-zinc-600">Run the seed and skills endpoint first.</p>
              </div>
            </div>
          )}
        </div>

        <div className="grid content-start gap-2">
          {topSkills.map((skill, index) => (
            <div
              key={`${skill.label}-${skill.name}`}
              className="rounded-md border border-white/10 bg-black/20 px-3 py-2"
            >
              <div className="flex items-center justify-between gap-3">
                <p className="truncate text-sm font-semibold text-white" title={skill.name}>
                  {index + 1}. {skill.name}
                </p>
                <span className="text-xs font-semibold text-cyan-200">
                  {Math.round(skill.strength_score)}
                </span>
              </div>
              <p className="mt-1 text-xs text-zinc-500">
                {skill.project_count} projects, {skill.paper_count} papers
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function shortenSkillName(name: string) {
  if (name.length <= 14) {
    return name;
  }

  return `${name.slice(0, 12)}...`;
}
