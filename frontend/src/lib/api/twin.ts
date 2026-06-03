export interface TwinProfile {
  id: string;
  name: string;
  repository_count: number;
  paper_count: number;
  skill_count: number;
  average_confidence: number;
}

export interface SkillStrength {
  name: string;
  label: "Technology" | "Framework" | string;
  confidence: number;
  strength_score: number;
  project_count: number;
  paper_count: number;
}

export interface SkillGapItem {
  name: string;
  label: "Technology" | "Framework" | string;
  paper_count: number;
  graph_count: number;
  relevance_score: number;
}

export interface SkillGapResponse {
  target_role: string;
  have: SkillStrength[];
  missing: SkillGapItem[];
}

export interface SkillEvolutionPoint {
  date: string;
  [skillName: string]: string | number;
}

const API_BASE_URL = "http://localhost:8000";

export async function fetchTwinProfile(): Promise<TwinProfile> {
  return getJson<TwinProfile>("/api/v1/twin/profile");
}

export async function fetchTwinSkills(limit = 50): Promise<SkillStrength[]> {
  const searchParams = new URLSearchParams({ limit: String(limit) });
  return getJson<SkillStrength[]>(`/api/v1/twin/skills?${searchParams.toString()}`);
}

export async function fetchSkillGap(targetRole = "MLEngineer"): Promise<SkillGapResponse> {
  const searchParams = new URLSearchParams({ target: targetRole });
  return getJson<SkillGapResponse>(`/api/v1/twin/skill-gap?${searchParams.toString()}`);
}

export async function fetchSkillEvolution(): Promise<SkillEvolutionPoint[]> {
  return getJson<SkillEvolutionPoint[]>("/api/v1/twin/evolution");
}

async function getJson<TResponse>(path: string): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
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

  return response.json() as Promise<TResponse>;
}

async function readErrorMessage(response: Response) {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Fall through to the status-based message.
  }

  return `Twin request failed with HTTP ${response.status}.`;
}
