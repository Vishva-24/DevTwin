export type AnalysisStatus = "pending" | "running" | "completed" | "failed";

export interface AnalyzeRepoRequest {
  github_url: string;
}

export interface AnalyzeRepoResponse {
  analysis_id: string;
  repository_id: string;
  status: AnalysisStatus;
  cached: boolean;
  message: string;
}

export interface AiSummary {
  project_purpose: string;
  architecture_summary: string;
  key_features: string[];
  strengths_and_weaknesses?: string;
}

export interface TechStack {
  languages?: string[];
  frameworks?: string[];
  databases?: string[];
  ai_tools?: string[];
  package_managers?: string[];
  infra?: string[];
  detection_notes?: string[];
}

export interface ApiRoute {
  method: string;
  path: string;
  file: string;
  line?: number;
  framework?: string;
  router_variable?: string | null;
  prefix?: string | null;
}

export interface ApiRoutes {
  routes: ApiRoute[];
  total_routes: number;
  files_scanned?: number;
  frameworks_found?: string[];
  detection_notes?: string[];
}

export interface DependencyNode {
  id: string;
  label?: string;
  package?: string;
  imports_count?: number;
  imported_by_count?: number;
  external_imports?: string[];
}

export interface DependencyEdge {
  source: string;
  target: string;
  import_statement?: string;
  line?: number;
}

export interface DependencyGraph {
  nodes?: DependencyNode[];
  edges: DependencyEdge[];
  external_packages?: Record<string, string[]>;
  total_internal_edges?: number;
  files_analyzed?: number;
  files_skipped?: number;
  parse_errors?: string[];
  detection_notes?: string[];
}

export type DependencyGraphPayload = DependencyGraph | DependencyEdge[];

export interface ArchitectureHints {
  primary_pattern?: string;
  description?: string;
  confidence?: "high" | "medium" | "low" | string;
  evidence?: unknown[];
  top_level_directories?: string[];
  detection_notes?: string[];
}

export interface RepoAnalysis {
  id: string;
  repository_id: string;
  status: AnalysisStatus;
  commit_hash?: string | null;
  error_message?: string | null;
  tech_stack?: TechStack | null;
  api_routes?: ApiRoutes | null;
  dependency_graph?: DependencyGraphPayload | null;
  architecture_hints?: ArchitectureHints | null;
  summary?: string | null;
  summary_json?: AiSummary | null;
  ai_summary?: AiSummary | null;
  created_at: string;
  completed_at?: string | null;
}

export type AnalyzeResult = AnalyzeRepoResponse | RepoAnalysis;

export interface PaperMetadata {
  id: string;
  title?: string | null;
  file_name: string;
  file_path?: string;
  authors?: string[];
  abstract?: string | null;
  total_pages?: number;
  page_count?: number;
  uploaded_at: string;
}

export interface PaperUploadResponse {
  paper_id: string;
  status: string;
  message: string;
  metadata: PaperMetadata;
}

export interface PaperChunkPipelineResponse {
  status: string;
  paper_id: string;
  total_chunks: number;
}

export interface PaperEmbeddingPipelineResponse {
  status: string;
  paper_id: string;
  total_vectors_stored: number;
  collection_name?: string;
  embedding_model?: string;
}

export interface ResearchManifest {
  manifest_version?: string;
  paper_id: string;
  title?: string | null;
  core_concepts: string[];
  methodology_summary: string;
  key_findings: string[];
  tech_stack_mentions: string[];
  generated_from_chunk_indices?: number[];
  source_chunk_count?: number;
  limitations?: string | null;
}

export interface ResearchChatRequest {
  query: string;
}

export interface ResearchChatResponse {
  answer: string;
  citations: number[];
}
