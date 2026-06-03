"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type {
  DependencyEdge,
  DependencyGraphPayload,
  DependencyNode,
} from "@/types/api";

type FileNodeData = {
  label: string;
  path: string;
  packageName?: string;
  importsCount?: number;
  importedByCount?: number;
  externalImports?: string[];
};

type FileFlowNode = Node<FileNodeData, "file">;

interface DependencyGraphProps {
  dependencyGraph?: DependencyGraphPayload | null;
  className?: string;
}

const NODE_WIDTH = 260;
const NODE_HEIGHT = 88;
const X_GAP = 340;
const Y_GAP = 128;

function FileNode({ data }: NodeProps<FileFlowNode>) {
  return (
    <div className="w-[260px] rounded-md border border-white/10 bg-zinc-950 px-3 py-2 text-left shadow-lg shadow-black/30">
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !border-zinc-950 !bg-emerald-300"
      />
      <div className="truncate text-sm font-semibold text-white" title={data.path}>
        {data.label}
      </div>
      <div className="mt-1 truncate font-mono text-xs text-zinc-500" title={data.path}>
        {data.packageName || data.path}
      </div>
      <div className="mt-2 flex gap-2 text-[11px] text-zinc-400">
        <span className="rounded-md border border-cyan-300/20 bg-cyan-300/10 px-2 py-0.5 text-cyan-100">
          imports {data.importsCount ?? 0}
        </span>
        <span className="rounded-md border border-emerald-300/20 bg-emerald-300/10 px-2 py-0.5 text-emerald-100">
          used by {data.importedByCount ?? 0}
        </span>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2 !w-2 !border-zinc-950 !bg-cyan-300"
      />
    </div>
  );
}

const nodeTypes: NodeTypes = {
  file: FileNode,
};

function filename(path: string) {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

function parentPackage(path: string) {
  const parts = path.split("/").filter(Boolean);
  return parts.length > 1 ? parts.slice(0, -1).join("/") : "";
}

function normalizeGraph(input?: DependencyGraphPayload | null) {
  const rawEdges = Array.isArray(input) ? input : input?.edges ?? [];
  const declaredNodes = Array.isArray(input) ? [] : input?.nodes ?? [];
  const nodeMap = new Map<string, DependencyNode>();
  const edgeMap = new Map<string, DependencyEdge>();

  for (const node of declaredNodes) {
    if (node.id) {
      nodeMap.set(node.id, node);
    }
  }

  for (const edge of rawEdges) {
    if (!edge.source || !edge.target || edge.source === edge.target) {
      continue;
    }

    const key = `${edge.source}->${edge.target}`;
    if (!edgeMap.has(key)) {
      edgeMap.set(key, edge);
    }

    if (!nodeMap.has(edge.source)) {
      nodeMap.set(edge.source, { id: edge.source, label: filename(edge.source) });
    }
    if (!nodeMap.has(edge.target)) {
      nodeMap.set(edge.target, { id: edge.target, label: filename(edge.target) });
    }
  }

  return {
    nodes: Array.from(nodeMap.values()).sort((a, b) => a.id.localeCompare(b.id)),
    edges: Array.from(edgeMap.values()).sort((a, b) =>
      `${a.source}${a.target}`.localeCompare(`${b.source}${b.target}`),
    ),
  };
}

function directedLayout(nodes: DependencyNode[], edges: DependencyEdge[]) {
  const ids = nodes.map((node) => node.id);
  const incoming = new Map(ids.map((id) => [id, 0]));
  const outgoing = new Map<string, string[]>();
  const levels = new Map(ids.map((id) => [id, 0]));

  for (const edge of edges) {
    if (!incoming.has(edge.source) || !incoming.has(edge.target)) {
      continue;
    }
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  }

  const queue = ids.filter((id) => (incoming.get(id) ?? 0) === 0).sort();
  const visited = new Set<string>();

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || visited.has(current)) {
      continue;
    }

    visited.add(current);
    const nextLevel = (levels.get(current) ?? 0) + 1;

    for (const target of outgoing.get(current) ?? []) {
      levels.set(target, Math.max(levels.get(target) ?? 0, nextLevel));
      incoming.set(target, Math.max(0, (incoming.get(target) ?? 0) - 1));
      if ((incoming.get(target) ?? 0) === 0) {
        queue.push(target);
        queue.sort();
      }
    }
  }

  for (const id of ids) {
    if (!visited.has(id)) {
      const connectedSource = edges.find((edge) => edge.target === id)?.source;
      levels.set(id, connectedSource ? (levels.get(connectedSource) ?? 0) + 1 : 0);
    }
  }

  const byLevel = new Map<number, DependencyNode[]>();
  for (const node of nodes) {
    const level = levels.get(node.id) ?? 0;
    byLevel.set(level, [...(byLevel.get(level) ?? []), node]);
  }

  const flowNodes: FileFlowNode[] = [];
  for (const [level, levelNodes] of Array.from(byLevel.entries()).sort((a, b) => a[0] - b[0])) {
    const sortedLevelNodes = levelNodes.sort((a, b) => a.id.localeCompare(b.id));
    sortedLevelNodes.forEach((node, index) => {
      flowNodes.push({
        id: node.id,
        type: "file",
        position: {
          x: level * X_GAP,
          y: index * Y_GAP,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: {
          label: node.label || filename(node.id),
          path: node.id,
          packageName: node.package || parentPackage(node.id),
          importsCount: node.imports_count,
          importedByCount: node.imported_by_count,
          externalImports: node.external_imports,
        },
        style: {
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
        },
      });
    });
  }

  return flowNodes;
}

function buildFlowEdges(edges: DependencyEdge[]): Edge[] {
  return edges.map((edge, index) => ({
    id: `${edge.source}->${edge.target}-${index}`,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: "#67e8f9",
    },
    style: {
      stroke: "#67e8f9",
      strokeOpacity: 0.58,
      strokeWidth: 1.6,
    },
    label: edge.line ? `line ${edge.line}` : undefined,
    labelStyle: {
      fill: "#a1a1aa",
      fontSize: 11,
    },
    labelBgStyle: {
      fill: "#09090b",
      fillOpacity: 0.85,
    },
  }));
}

export function DependencyGraph({ dependencyGraph, className = "" }: DependencyGraphProps) {
  const graph = useMemo(() => normalizeGraph(dependencyGraph), [dependencyGraph]);
  const nodes = useMemo(() => directedLayout(graph.nodes, graph.edges), [graph]);
  const edges = useMemo(() => buildFlowEdges(graph.edges), [graph.edges]);

  if (graph.nodes.length === 0) {
    return (
      <section
        className={`rounded-lg border border-white/10 bg-zinc-950 p-5 text-zinc-100 shadow-2xl shadow-black/30 ${className}`}
      >
        <h2 className="text-sm font-semibold text-white">Dependency Graph</h2>
        <p className="mt-2 text-sm text-zinc-500">
          No internal dependency edges were detected for this repository.
        </p>
      </section>
    );
  }

  return (
    <section
      className={`overflow-hidden rounded-lg border border-white/10 bg-zinc-950 text-zinc-100 shadow-2xl shadow-black/30 ${className}`}
    >
      <div className="flex flex-col gap-2 border-b border-white/10 px-5 py-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-white">Dependency Graph</h2>
          <p className="mt-1 text-xs text-zinc-500">
            {graph.nodes.length} files, {graph.edges.length} directed imports
          </p>
        </div>
        <p className="text-xs text-zinc-500">Source files flow toward their dependencies.</p>
      </div>

      <div className="h-[560px] w-full bg-black/30">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          minZoom={0.2}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#3f3f46" gap={24} size={1} />
          <MiniMap
            pannable
            zoomable
            className="!border !border-white/10 !bg-zinc-950/90"
            nodeColor="#67e8f9"
            maskColor="rgba(9, 9, 11, 0.72)"
          />
          <Controls className="!border !border-white/10 !bg-zinc-950/90 !shadow-none" />
        </ReactFlow>
      </div>
    </section>
  );
}
