export interface GraphNode {
  id: string;
  label: "Repository" | "Paper" | "Technology" | "Framework" | "Concept" | string;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: "USES" | "EXPLAINS_CONCEPT" | "MENTIONS_TECH" | string;
  properties: Record<string, unknown>;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

const API_BASE_URL = "http://localhost:8000";

export async function fetchKnowledgeGraph(params?: {
  nodeName?: string | null;
  limit?: number;
}): Promise<GraphResponse> {
  const searchParams = new URLSearchParams();
  const limit = params?.limit ?? 50;

  searchParams.set("limit", String(limit));

  if (params?.nodeName?.trim()) {
    searchParams.set("node_name", params.nodeName.trim());
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/graph/explore?${searchParams.toString()}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new Error(message);
  }

  return response.json() as Promise<GraphResponse>;
}

async function readErrorMessage(response: Response) {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Fall through to a generic status-based message.
  }

  return `Graph request failed with HTTP ${response.status}.`;
}
