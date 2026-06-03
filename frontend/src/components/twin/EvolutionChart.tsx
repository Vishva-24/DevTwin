"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Loader2, TrendingUp } from "lucide-react";

import { fetchSkillEvolution, type SkillEvolutionPoint } from "@/lib/api/twin";

const SKILL_COLORS = [
  "#67e8f9",
  "#86efac",
  "#c4b5fd",
  "#fbbf24",
  "#fda4af",
  "#93c5fd",
  "#f0abfc",
  "#5eead4",
];

export function EvolutionChart() {
  const [data, setData] = useState<SkillEvolutionPoint[]>([]);
  const [mounted, setMounted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMounted(true);
    let isActive = true;

    async function loadEvolution() {
      setLoading(true);
      setError(null);

      try {
        const response = await fetchSkillEvolution();
        if (isActive) {
          setData(response);
        }
      } catch (caught) {
        if (isActive) {
          setError(
            caught instanceof Error ? caught.message : "Unable to load temporal skill evolution.",
          );
        }
      } finally {
        if (isActive) {
          setLoading(false);
        }
      }
    }

    void loadEvolution();

    return () => {
      isActive = false;
    };
  }, []);

  const skillKeys = useMemo(() => extractSkillKeys(data), [data]);

  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-zinc-950 text-zinc-100 shadow-2xl shadow-black/30">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md border border-emerald-300/20 bg-emerald-300/10">
            <TrendingUp className="h-5 w-5 text-emerald-200" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-semibold text-cyan-200">Temporal Evolution</p>
            <h2 className="text-lg font-semibold text-white">Skill growth</h2>
          </div>
        </div>
        <span className="rounded-md border border-white/10 px-2 py-1 text-xs text-zinc-500">
          {skillKeys.length} skills
        </span>
      </div>

      {error ? (
        <div className="border-b border-red-300/20 bg-red-300/10 px-4 py-3 text-sm text-red-100">
          {error}
        </div>
      ) : null}

      <div className="relative h-[420px] p-4">
        {loading ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-zinc-950/70">
            <div className="flex items-center gap-2 text-sm text-zinc-300">
              <Loader2 className="h-4 w-4 animate-spin text-cyan-200" aria-hidden="true" />
              Loading evolution
            </div>
          </div>
        ) : null}

        {!loading && data.length === 0 ? (
          <div className="grid h-full place-items-center rounded-lg border border-dashed border-white/10 bg-black/20 px-6 text-center">
            <div>
              <p className="text-sm font-semibold text-zinc-200">No temporal graph data</p>
              <p className="mt-2 text-sm leading-6 text-zinc-600">
                Add dated repository and paper nodes to Neo4j for a richer timeline.
              </p>
            </div>
          </div>
        ) : mounted ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ left: 2, right: 16, top: 18, bottom: 6 }}>
              <defs>
                {skillKeys.map((skill, index) => {
                  const color = SKILL_COLORS[index % SKILL_COLORS.length];
                  return (
                    <linearGradient
                      key={skill}
                      id={gradientId(skill)}
                      x1="0"
                      x2="0"
                      y1="0"
                      y2="1"
                    >
                      <stop offset="5%" stopColor={color} stopOpacity={0.62} />
                      <stop offset="95%" stopColor={color} stopOpacity={0.08} />
                    </linearGradient>
                  );
                })}
              </defs>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: "#a1a1aa", fontSize: 12 }}
                axisLine={{ stroke: "rgba(255,255,255,0.12)" }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: "#71717a", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={36}
              />
              <Tooltip
                formatter={(value, name) => [`${Math.round(Number(value))}%`, name]}
                labelFormatter={(label) => `Year ${label}`}
                contentStyle={{
                  background: "#09090b",
                  border: "1px solid rgba(255,255,255,0.12)",
                  borderRadius: 8,
                  color: "#f4f4f5",
                }}
                itemStyle={{ color: "#e4e4e7" }}
                cursor={{ stroke: "#67e8f9", strokeOpacity: 0.35 }}
              />
              {skillKeys.map((skill, index) => {
                const color = SKILL_COLORS[index % SKILL_COLORS.length];
                return (
                  <Area
                    key={skill}
                    type="monotone"
                    dataKey={skill}
                    stackId="skills"
                    stroke={color}
                    strokeWidth={2}
                    fill={`url(#${gradientId(skill)})`}
                    fillOpacity={1}
                    dot={false}
                    activeDot={{ r: 4, stroke: "#09090b", strokeWidth: 2 }}
                  />
                );
              })}
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full rounded-lg border border-white/10 bg-black/20" />
        )}
      </div>

      {skillKeys.length > 0 ? (
        <div className="flex flex-wrap gap-2 border-t border-white/10 px-4 py-3">
          {skillKeys.map((skill, index) => (
            <span
              key={skill}
              className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-black/20 px-2.5 py-1 text-xs font-medium text-zinc-300"
            >
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: SKILL_COLORS[index % SKILL_COLORS.length] }}
              />
              {skill}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function extractSkillKeys(data: SkillEvolutionPoint[]) {
  const keys = new Set<string>();
  for (const row of data) {
    for (const key of Object.keys(row)) {
      if (key !== "date") {
        keys.add(key);
      }
    }
  }
  return Array.from(keys);
}

function gradientId(skill: string) {
  return `skill-gradient-${skill.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}
