import { MarkerType, Position, type Edge, type Node } from "@xyflow/react";

import type { GraphEdge, GraphNode } from "@/lib/api/graph";

export type KnowledgeGraphNodeData = {
  label: string;
  primaryText: string;
  secondaryText?: string;
  lookupName: string;
  properties: Record<string, unknown>;
} & Record<string, unknown>;

export type KnowledgeGraphFlowNode = Node<KnowledgeGraphNodeData, "knowledge">;

const CENTER_X = 0;
const CENTER_Y = 0;
const RADIUS_STEP = 330;
const NODE_WIDTH = 250;
const NODE_HEIGHT = 112;

const LABEL_RANK: Record<string, number> = {
  Repository: 0,
  Paper: 0,
  Framework: 1,
  Technology: 1,
  Concept: 2,
};

const EDGE_COLORS: Record<string, string> = {
  USES: "#67e8f9",
  EXPLAINS_CONCEPT: "#fbbf24",
  MENTIONS_TECH: "#86efac",
};

export function buildFlowNodes(graphNodes: GraphNode[]): KnowledgeGraphFlowNode[] {
  const sortedNodes = [...graphNodes].sort((a, b) => {
    const rankDelta = (LABEL_RANK[a.label] ?? 3) - (LABEL_RANK[b.label] ?? 3);
    return rankDelta || getPrimaryText(a).localeCompare(getPrimaryText(b));
  });

  const groups = groupByRank(sortedNodes);
  const positioned: KnowledgeGraphFlowNode[] = [];

  for (const [rank, nodes] of groups.entries()) {
    const radius = rank === 0 ? 0 : RADIUS_STEP * rank;
    const angleOffset = rank % 2 === 0 ? Math.PI / 8 : 0;

    nodes.forEach((node, index) => {
      const angle = nodes.length <= 1 ? 0 : (Math.PI * 2 * index) / nodes.length + angleOffset;
      const position =
        rank === 0
          ? {
              x: CENTER_X + (index - (nodes.length - 1) / 2) * 320,
              y: CENTER_Y,
            }
          : {
              x: CENTER_X + Math.cos(angle) * radius,
              y: CENTER_Y + Math.sin(angle) * radius,
            };

      positioned.push({
        id: node.id,
        type: "knowledge",
        position,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: {
          label: node.label,
          primaryText: getPrimaryText(node),
          secondaryText: getSecondaryText(node),
          lookupName: getLookupName(node),
          properties: node.properties,
        },
        style: {
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
        },
      });
    });
  }

  return positioned;
}

export function buildFlowEdges(graphEdges: GraphEdge[]): Edge[] {
  return graphEdges.map((edge) => {
    const color = EDGE_COLORS[edge.type] ?? "#a1a1aa";

    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      label: edge.type,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color,
      },
      style: {
        stroke: color,
        strokeOpacity: 0.68,
        strokeWidth: 1.8,
      },
      labelStyle: {
        fill: "#e4e4e7",
        fontSize: 10,
        fontWeight: 700,
      },
      labelBgPadding: [8, 4],
      labelBgBorderRadius: 4,
      labelBgStyle: {
        fill: "#09090b",
        fillOpacity: 0.86,
      },
    } satisfies Edge;
  });
}

export function getPrimaryText(node: GraphNode) {
  return (
    stringProperty(node.properties.name) ??
    stringProperty(node.properties.title) ??
    stringProperty(node.properties.repo_name) ??
    stringProperty(node.properties.id) ??
    node.id
  );
}

function getLookupName(node: GraphNode) {
  return (
    stringProperty(node.properties.name) ??
    stringProperty(node.properties.title) ??
    stringProperty(node.properties.repo_name) ??
    stringProperty(node.properties.id) ??
    node.id
  );
}

function getSecondaryText(node: GraphNode) {
  if (node.label === "Repository") {
    return stringProperty(node.properties.github_url);
  }

  if (node.label === "Paper") {
    return stringProperty(node.properties.filename);
  }

  return stringProperty(node.properties.category);
}

function groupByRank(nodes: GraphNode[]) {
  const groups = new Map<number, GraphNode[]>();
  for (const node of nodes) {
    const rank = LABEL_RANK[node.label] ?? 3;
    groups.set(rank, [...(groups.get(rank) ?? []), node]);
  }
  return groups;
}

function stringProperty(value: unknown) {
  return typeof value === "string" && value.trim() ? value : undefined;
}
