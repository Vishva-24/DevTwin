"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type NodeMouseHandler,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2, Network, RotateCcw, Search } from "lucide-react";

import { fetchKnowledgeGraph, type GraphResponse } from "@/lib/api/graph";
import { CustomNode } from "@/components/graph/CustomNode";
import {
  buildFlowEdges,
  buildFlowNodes,
  type KnowledgeGraphFlowNode,
} from "@/components/graph/layout";

const nodeTypes = {
  knowledge: CustomNode,
} satisfies NodeTypes;

export function GraphCanvas() {
  const [graph, setGraph] = useState<GraphResponse>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeNodeName, setActiveNodeName] = useState<string | null>(null);
  const [searchValue, setSearchValue] = useState("");

  const flowNodes = useMemo(() => buildFlowNodes(graph.nodes), [graph.nodes]);
  const flowEdges = useMemo(() => buildFlowEdges(graph.edges), [graph.edges]);

  const loadGraph = useCallback(async (nodeName?: string | null) => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetchKnowledgeGraph({
        nodeName,
        limit: nodeName ? 80 : 50,
      });
      setGraph(response);
      setActiveNodeName(nodeName?.trim() || null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load the knowledge graph.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadGraph(null);
  }, [loadGraph]);

  const handleNodeClick: NodeMouseHandler<KnowledgeGraphFlowNode> = useCallback(
    (_event, node) => {
      void loadGraph(node.data.lookupName);
      setSearchValue(node.data.lookupName);
    },
    [loadGraph],
  );

  function handleSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadGraph(searchValue);
  }

  function handleReset() {
    setSearchValue("");
    void loadGraph(null);
  }

  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-zinc-950 text-zinc-100 shadow-2xl shadow-black/30">
      <div className="flex flex-col gap-4 border-b border-white/10 px-4 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-md border border-cyan-300/20 bg-cyan-300/10">
            <Network className="h-5 w-5 text-cyan-200" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-semibold text-white">
              {activeNodeName ? `Focused on ${activeNodeName}` : "Graph Overview"}
            </p>
            <p className="mt-1 text-xs text-zinc-500">
              {graph.nodes.length} nodes, {graph.edges.length} edges
            </p>
          </div>
        </div>

        <form onSubmit={handleSearch} className="flex flex-col gap-2 sm:flex-row">
          <label className="relative min-w-0 sm:w-80">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-600" />
            <input
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="Search node name"
              className="h-10 w-full rounded-md border border-white/10 bg-black/30 pl-9 pr-3 text-sm text-white outline-none transition placeholder:text-zinc-600 focus:border-cyan-300/40"
            />
          </label>
          <button
            type="submit"
            disabled={loading}
            className="inline-flex h-10 items-center justify-center rounded-md bg-cyan-300 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Explore
          </button>
          <button
            type="button"
            onClick={handleReset}
            disabled={loading}
            className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-white/10 text-zinc-400 transition hover:bg-white/[0.05] hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Reset graph"
            title="Reset graph"
          >
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
          </button>
        </form>
      </div>

      {error ? (
        <div className="border-b border-red-300/20 bg-red-300/10 px-4 py-3 text-sm text-red-100">
          {error}
        </div>
      ) : null}

      <div className="relative h-[720px] w-full bg-black">
        {loading ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-zinc-950/70 backdrop-blur-sm">
            <div className="flex items-center gap-3 rounded-md border border-white/10 bg-zinc-950 px-4 py-3 text-sm text-zinc-300">
              <Loader2 className="h-4 w-4 animate-spin text-cyan-200" aria-hidden="true" />
              Loading graph
            </div>
          </div>
        ) : null}

        {flowNodes.length === 0 && !loading ? (
          <div className="absolute inset-0 z-10 grid place-items-center">
            <div className="text-center">
              <p className="text-sm font-semibold text-zinc-200">No graph data returned</p>
              <p className="mt-2 text-sm text-zinc-600">Try another node name or rerun ingestion.</p>
            </div>
          </div>
        ) : null}

        <ReactFlow
          key={activeNodeName ?? "overview"}
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onNodeClick={handleNodeClick}
          fitView
          fitViewOptions={{ padding: 0.22 }}
          minZoom={0.12}
          maxZoom={1.8}
          nodesDraggable
          nodesConnectable={false}
          elementsSelectable
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#3f3f46" gap={28} size={1} />
          <MiniMap
            pannable
            zoomable
            className="!border !border-white/10 !bg-zinc-950/90"
            nodeColor={(node) => {
              const label = String(node.data?.label ?? "");
              if (label === "Repository") return "#7dd3fc";
              if (label === "Paper") return "#c4b5fd";
              if (label === "Technology") return "#86efac";
              if (label === "Framework") return "#67e8f9";
              if (label === "Concept") return "#fbbf24";
              return "#a1a1aa";
            }}
            maskColor="rgba(9, 9, 11, 0.72)"
          />
          <Controls className="!border !border-white/10 !bg-zinc-950/90 !shadow-none" />
        </ReactFlow>
      </div>
    </section>
  );
}
