export interface ChatRequest {
  query: string;
}

export interface ChatResponse {
  answer: string;
  used_agents: string[];
  context_snippets: {
    graph?: string[];
    code?: string[];
    research?: string[];
    [key: string]: string[] | undefined;
  };
}

const API_BASE_URL = "http://localhost:8000";

export async function invokeAgentChat(request: ChatRequest): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/chat/invoke`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new Error(message);
  }

  return response.json() as Promise<ChatResponse>;
}

async function readErrorMessage(response: Response) {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Fall through to status-based message.
  }

  return `Chat request failed with HTTP ${response.status}.`;
}
