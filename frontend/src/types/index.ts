// ── Shared types matching backend Pydantic schemas ───────────────────────────

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  created_at: string;
}

export type Topic = "detection" | "remediation" | "custom";
export type ProjectStatus =
  | "draft"
  | "collecting"
  | "deduplicating"
  | "filtering"
  | "excluding"
  | "finding_links"
  | "completed"
  | "failed";

export type Relevance = "pending" | "keep" | "remove";
export type JobType =
  | "collect"
  | "deduplicate"
  | "filter"
  | "automated_exclusion"
  | "find_links"
  | "full_pipeline"
  | "custom_pipeline";
export type JobStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface SearchString {
  id: string;
  label: string;
  query: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  topic: Topic;
  year_start: number;
  year_end: number;
  sources: string;
  status: ProjectStatus;
  total_collected: number;
  total_after_dedup: number;
  total_after_filter: number;
  total_with_pdf: number;
  search_strings: SearchString[];
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  name: string;
  description?: string;
  topic: Topic;
  year_start?: number;
  year_end?: number;
  sources?: string;
  use_preset_strings?: boolean;
  search_strings?: { label: string; query: string }[];
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  year_start?: number;
  year_end?: number;
  sources?: string;
}

export interface Paper {
  id: string;
  title: string;
  authors: string;
  year: number | null;
  publisher: string;
  doi: string;
  link: string;
  abstract: string;
  source: string;
  search_string_label: string;
  is_duplicate: boolean;
  duplicate_of_id: string | null;
  relevance: Relevance;
  pdf_link: string;
  pdf_source: string;
  created_at: string;
}

export interface PaperBrief {
  id: string;
  title: string;
  authors: string;
  year: number | null;
  source: string;
  doi: string;
  is_duplicate: boolean;
  relevance: Relevance;
  pdf_link: string;
}

export interface PaperFilters {
  source?: string;
  year?: number;
  relevance?: Relevance;
  is_duplicate?: boolean;
  has_pdf?: boolean;
  search?: string;
}

export interface PaperStats {
  total: number;
  by_source: Record<string, number>;
  by_year: Record<string, number>;
  by_relevance: Record<string, number>;
  duplicates: number;
  with_pdf: number;
}

export interface Job {
  id: string;
  project_id: string;
  job_type: JobType;
  status: JobStatus;
  progress: number;
  message: string;
  celery_task_id: string;
  config: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

// WebSocket progress event
export interface ProgressEvent {
  job_id: string;
  project_id: string;
  progress: number;
  message: string;
  status: JobStatus;
}
