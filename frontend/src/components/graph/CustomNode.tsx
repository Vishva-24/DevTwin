"use client";

import { memo } from "react";
import { BookOpen, Boxes, Cpu, FileText, GitBranch, type LucideIcon } from "lucide-react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

import type { KnowledgeGraphFlowNode } from "@/components/graph/layout";

const NODE_THEME: Record<
  string,
  {
    icon: LucideIcon;
    border: string;
    accent: string;
    background: string;
    pill: string;
  }
> = {
  Repository: {
    icon: GitBranch,
    border: "border-sky-300/30",
    accent: "text-sky-200",
    background: "bg-sky-300/10",
    pill: "bg-sky-300/10 text-sky-100 ring-sky-300/20",
  },
  Paper: {
    icon: FileText,
    border: "border-violet-300/30",
    accent: "text-violet-200",
    background: "bg-violet-300/10",
    pill: "bg-violet-300/10 text-violet-100 ring-violet-300/20",
  },
  Technology: {
    icon: Cpu,
    border: "border-emerald-300/30",
    accent: "text-emerald-200",
    background: "bg-emerald-300/10",
    pill: "bg-emerald-300/10 text-emerald-100 ring-emerald-300/20",
  },
  Framework: {
    icon: Boxes,
    border: "border-cyan-300/30",
    accent: "text-cyan-200",
    background: "bg-cyan-300/10",
    pill: "bg-cyan-300/10 text-cyan-100 ring-cyan-300/20",
  },
  Concept: {
    icon: BookOpen,
    border: "border-amber-300/30",
    accent: "text-amber-200",
    background: "bg-amber-300/10",
    pill: "bg-amber-300/10 text-amber-100 ring-amber-300/20",
  },
};

const DEFAULT_THEME = {
  icon: Cpu,
  border: "border-zinc-300/20",
  accent: "text-zinc-200",
  background: "bg-zinc-300/10",
  pill: "bg-zinc-300/10 text-zinc-100 ring-zinc-300/20",
};

function CustomNodeComponent({ data, selected }: NodeProps<KnowledgeGraphFlowNode>) {
  const theme = NODE_THEME[data.label] ?? DEFAULT_THEME;
  const Icon = theme.icon;

  return (
    <div
      className={`w-[250px] rounded-lg border ${theme.border} bg-zinc-950/95 px-3 py-3 text-left shadow-2xl shadow-black/40 transition ${
        selected ? "ring-2 ring-cyan-200/70" : "ring-1 ring-white/5"
      }`}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-zinc-950 !bg-cyan-200"
      />

      <div className="flex items-start gap-3">
        <div
          className={`grid h-9 w-9 shrink-0 place-items-center rounded-md border border-white/10 ${theme.background}`}
        >
          <Icon className={`h-4 w-4 ${theme.accent}`} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${theme.pill}`}>
              {data.label}
            </span>
          </div>
          <p className="mt-2 line-clamp-2 text-sm font-semibold leading-snug text-white" title={data.primaryText}>
            {data.primaryText}
          </p>
          {data.secondaryText ? (
            <p className="mt-1 truncate text-[11px] text-zinc-500" title={data.secondaryText}>
              {data.secondaryText}
            </p>
          ) : null}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!h-2.5 !w-2.5 !border-zinc-950 !bg-emerald-200"
      />
    </div>
  );
}

export const CustomNode = memo(CustomNodeComponent);
