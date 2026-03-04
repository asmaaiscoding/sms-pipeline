import api from "./client";
import type {
  User,
  TokenResponse,
  Project,
  ProjectCreate,
  ProjectUpdate,
  Paper,
  PaperBrief,
  PaperFilters,
  PaperStats,
  Job,
  Paginated,
  JobType,
} from "@/types";

// ── Auth ─────────────────────────────────────────────────────────────────────
export const authApi = {
  register: (email: string, password: string, full_name: string) =>
    api.post<TokenResponse>("/auth/register", {
      email,
      password,
      full_name,
    }),

  login: (email: string, password: string) =>
    api.post<TokenResponse>("/auth/login", { email, password }),

  me: () => api.get<User>("/auth/me"),

  refresh: (refresh_token: string) =>
    api.post<TokenResponse>("/auth/refresh", { refresh_token }),
};

// ── Projects ─────────────────────────────────────────────────────────────────
export const projectsApi = {
  list: (page = 1, perPage = 20) =>
    api.get<Paginated<Project>>("/projects", {
      params: { page, per_page: perPage },
    }),

  get: (id: string) => api.get<Project>(`/projects/${id}`),

  create: (data: ProjectCreate) => api.post<Project>("/projects", data),

  update: (id: string, data: ProjectUpdate) =>
    api.patch<Project>(`/projects/${id}`, data),

  delete: (id: string) => api.delete(`/projects/${id}`),
};

// ── Papers ───────────────────────────────────────────────────────────────────
export const papersApi = {
  list: (
    projectId: string,
    page = 1,
    perPage = 50,
    filters?: PaperFilters
  ) =>
    api.get<Paginated<PaperBrief>>(`/projects/${projectId}/papers`, {
      params: { page, per_page: perPage, ...filters },
    }),

  get: (projectId: string, paperId: string) =>
    api.get<Paper>(`/projects/${projectId}/papers/${paperId}`),

  update: (
    projectId: string,
    paperId: string,
    data: Partial<Pick<Paper, "relevance" | "pdf_link">>
  ) => api.patch<Paper>(`/projects/${projectId}/papers/${paperId}`, data),

  stats: (projectId: string) =>
    api.get<PaperStats>(`/projects/${projectId}/papers/stats`),

  filteringPrompt: (projectId: string) =>
    api.get<{ prompt: string; total_papers: number; instructions: string }>(
      `/projects/${projectId}/papers/filtering-prompt`
    ),
};

// ── Jobs ─────────────────────────────────────────────────────────────────────
export const jobsApi = {
  list: (projectId: string, page = 1, perPage = 20) =>
    api.get<Paginated<Job>>(`/projects/${projectId}/jobs`, {
      params: { page, per_page: perPage },
    }),

  create: (projectId: string, jobType: JobType, steps?: string[]) =>
    api.post<Job>(`/projects/${projectId}/jobs`, {
      job_type: jobType,
      steps: steps,
    }),

  get: (projectId: string, jobId: string) =>
    api.get<Job>(`/projects/${projectId}/jobs/${jobId}`),

  cancel: (projectId: string, jobId: string) =>
    api.post(`/projects/${projectId}/jobs/${jobId}/cancel`),
};

// ── File Upload ──────────────────────────────────────────────────────────────
export const uploadApi = {
  uploadPapers: (projectId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post<{ imported: number; columns_mapped: Record<string, string>; columns_unmapped: string[] }>(
      `/projects/${projectId}/papers/upload`,
      formData,
      { headers: { "Content-Type": "multipart/form-data" } }
    );
  },
};

// ── Exports ──────────────────────────────────────────────────────────────────
export const exportsApi = {
  download: (
    projectId: string,
    format: "xlsx" | "csv" = "xlsx",
    filters?: { include_duplicates?: boolean; relevance_filter?: string }
  ) =>
    api.get(`/projects/${projectId}/export`, {
      params: { format, ...filters },
      responseType: "blob",
    }),
};
