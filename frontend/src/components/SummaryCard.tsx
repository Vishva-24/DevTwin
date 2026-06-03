import { Layers3, Route, Sparkles } from "lucide-react";

import type { AiSummary, ApiRoute, RepoAnalysis, TechStack } from "@/types/api";

interface SummaryCardProps {
  analysis: RepoAnalysis;
  className?: string;
}

type StackGroup = {
  label: string;
  values: string[];
  className: string;
};

const methodStyles: Record<string, string> = {
  GET: "border-emerald-300/30 bg-emerald-300/10 text-emerald-200",
  POST: "border-cyan-300/30 bg-cyan-300/10 text-cyan-200",
  PUT: "border-amber-300/30 bg-amber-300/10 text-amber-200",
  PATCH: "border-violet-300/30 bg-violet-300/10 text-violet-200",
  DELETE: "border-red-300/30 bg-red-300/10 text-red-200",
  OPTIONS: "border-zinc-300/30 bg-zinc-300/10 text-zinc-200",
  HEAD: "border-zinc-300/30 bg-zinc-300/10 text-zinc-200",
};

function getSummary(analysis: RepoAnalysis): AiSummary | null {
  if (analysis.summary_json) {
    return analysis.summary_json;
  }
  if (analysis.ai_summary) {
    return analysis.ai_summary;
  }
  if (analysis.summary) {
    return {
      project_purpose: analysis.summary,
      architecture_summary: "Architecture summary is not available yet.",
      key_features: [],
    };
  }
  return null;
}

function compact(values: string[] | undefined) {
  return Array.from(new Set((values ?? []).filter(Boolean)));
}

function buildStackGroups(techStack: TechStack | null | undefined): StackGroup[] {
  return [
    {
      label: "Languages",
      values: compact(techStack?.languages),
      className: "border-sky-300/25 bg-sky-300/10 text-sky-100",
    },
    {
      label: "Frameworks",
      values: compact(techStack?.frameworks),
      className: "border-emerald-300/25 bg-emerald-300/10 text-emerald-100",
    },
    {
      label: "Databases",
      values: compact(techStack?.databases),
      className: "border-amber-300/25 bg-amber-300/10 text-amber-100",
    },
    {
      label: "AI",
      values: compact(techStack?.ai_tools),
      className: "border-fuchsia-300/25 bg-fuchsia-300/10 text-fuchsia-100",
    },
    {
      label: "Infra",
      values: compact(techStack?.infra),
      className: "border-zinc-300/25 bg-zinc-300/10 text-zinc-100",
    },
    {
      label: "Packages",
      values: compact(techStack?.package_managers),
      className: "border-indigo-300/25 bg-indigo-300/10 text-indigo-100",
    },
  ].filter((group) => group.values.length > 0);
}

function routePath(route: ApiRoute) {
  if (!route.prefix) {
    return route.path;
  }
  return `${route.prefix}${route.path}`.replace(/\/{2,}/g, "/");
}

export function SummaryCard({ analysis, className = "" }: SummaryCardProps) {
  const summary = getSummary(analysis);
  const stackGroups = buildStackGroups(analysis.tech_stack);
  const routes = analysis.api_routes?.routes ?? [];

  return (
    <article
      className={`overflow-hidden rounded-lg border border-white/10 bg-zinc-950 text-zinc-100 shadow-2xl shadow-black/30 ${className}`}
    >
      <div className="border-b border-white/10 px-5 py-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium text-cyan-200">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
              AI Summary
            </div>
            <h2 className="mt-2 text-xl font-semibold text-white">
              Repository Intelligence
            </h2>
          </div>
          <div className="rounded-md border border-white/10 px-3 py-2 text-xs text-zinc-400">
            {analysis.status}
          </div>
        </div>
      </div>

      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.85fr)]">
        <section className="border-b border-white/10 p-5 lg:border-b-0 lg:border-r">
          <div className="space-y-5">
            <div>
              <h3 className="text-sm font-semibold text-white">Project Purpose</h3>
              <p className="mt-2 text-sm leading-6 text-zinc-300">
                {summary?.project_purpose ?? "No AI summary is available for this analysis."}
              </p>
            </div>

            <div>
              <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
                <Layers3 className="h-4 w-4 text-emerald-200" aria-hidden="true" />
                Architecture
              </h3>
              <p className="mt-2 text-sm leading-6 text-zinc-300">
                {summary?.architecture_summary ??
                  analysis.architecture_hints?.description ??
                  "Architecture details are not available yet."}
              </p>
              {analysis.architecture_hints?.primary_pattern ? (
                <p className="mt-2 text-xs text-zinc-500">
                  Pattern: {analysis.architecture_hints.primary_pattern}
                </p>
              ) : null}
            </div>

            <div>
              <h3 className="text-sm font-semibold text-white">Key Features</h3>
              {summary?.key_features?.length ? (
                <ul className="mt-3 grid gap-2">
                  {summary.key_features.map((feature) => (
                    <li
                      key={feature}
                      className="rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-sm leading-5 text-zinc-300"
                    >
                      {feature}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-zinc-500">No inferred features were returned.</p>
              )}
            </div>
          </div>
        </section>

        <section className="p-5">
          <h3 className="text-sm font-semibold text-white">Tech Stack</h3>
          {stackGroups.length ? (
            <div className="mt-3 space-y-4">
              {stackGroups.map((group) => (
                <div key={group.label}>
                  <p className="mb-2 text-xs font-medium uppercase text-zinc-500">
                    {group.label}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {group.values.map((value) => (
                      <span
                        key={`${group.label}-${value}`}
                        className={`inline-flex max-w-full items-center rounded-md border px-2.5 py-1 text-xs font-medium ${group.className}`}
                      >
                        {value}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-zinc-500">No stack signals were detected.</p>
          )}
        </section>
      </div>

      <section className="border-t border-white/10 p-5">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
            <Route className="h-4 w-4 text-cyan-200" aria-hidden="true" />
            API Routes
          </h3>
          <p className="text-xs text-zinc-500">
            {analysis.api_routes?.total_routes ?? routes.length} detected
          </p>
        </div>

        {routes.length ? (
          <div className="mt-4 overflow-x-auto rounded-md border border-white/10">
            <table className="min-w-full divide-y divide-white/10 text-left text-sm">
              <thead className="bg-white/[0.03] text-xs uppercase text-zinc-500">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Method
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Path
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Framework
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    File
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {routes.map((route, index) => {
                  const method = route.method.toUpperCase();

                  return (
                    <tr key={`${method}-${route.file}-${route.path}-${index}`}>
                      <td className="whitespace-nowrap px-4 py-3">
                        <span
                          className={`inline-flex rounded-md border px-2 py-1 text-xs font-semibold ${
                            methodStyles[method] ?? methodStyles.OPTIONS
                          }`}
                        >
                          {method}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-zinc-200">
                        {routePath(route)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-zinc-400">
                        {route.framework ?? "-"}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-zinc-500">
                        {route.line ? `${route.file}:${route.line}` : route.file}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-3 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-500">
            No API routes were detected for this repository.
          </p>
        )}
      </section>
    </article>
  );
}
