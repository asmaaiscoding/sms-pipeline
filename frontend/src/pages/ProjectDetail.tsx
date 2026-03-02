import { useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { projectsApi, papersApi, jobsApi, exportsApi } from "@/api/endpoints";
import { getErrorMessage } from "@/api/client";
import { useWebSocket } from "@/hooks/useWebSocket";
import {
  PageSpinner,
  StatusBadge,
  StatCard,
  ProgressBar,
  EmptyState,
} from "@/components/ui";
import {
  ArrowLeft,
  Play,
  Download,
  FileText,
  Filter,
  Copy,
  CheckCircle2,
  Link as LinkIcon,
  Layers,
  ChevronLeft,
  ChevronRight,
  Search,
  XCircle,
  ClipboardCopy,
  MessageSquare,
  StopCircle,
  Trash2,
} from "lucide-react";
import toast from "react-hot-toast";
import type { ProgressEvent, JobType, PaperFilters } from "@/types";

const PIPELINE_STEPS: { type: JobType; label: string; icon: React.ElementType; desc: string }[] = [
  { type: "collect", label: "Collect", icon: Layers, desc: "Scrape papers from selected sources" },
  { type: "deduplicate", label: "Deduplicate", icon: Copy, desc: "Find and mark duplicates" },
  { type: "find_links", label: "Find PDFs", icon: LinkIcon, desc: "Locate open-access PDF links" },
];

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // ── State ────────────────────────────────────────────────────────────────
  const [activeJob, setActiveJob] = useState<{
    id: string;
    type: JobType;
    progress: number;
    message: string;
    status: string;
  } | null>(null);

  const [paperPage, setPaperPage] = useState(1);
  const [filters, setFilters] = useState<PaperFilters>({});
  const [searchInput, setSearchInput] = useState("");
  const [showFilterPrompt, setShowFilterPrompt] = useState(false);

  // ── Queries ──────────────────────────────────────────────────────────────
  const projectQuery = useQuery({
    queryKey: ["project", id],
    queryFn: () => projectsApi.get(id!),
    select: (res) => res.data,
    enabled: !!id,
  });

  const papersQuery = useQuery({
    queryKey: ["papers", id, paperPage, filters],
    queryFn: () => papersApi.list(id!, paperPage, 50, filters),
    select: (res) => res.data,
    enabled: !!id,
  });

  const statsQuery = useQuery({
    queryKey: ["paper-stats", id],
    queryFn: () => papersApi.stats(id!),
    select: (res) => res.data,
    enabled: !!id,
  });

  const promptQuery = useQuery({
    queryKey: ["filtering-prompt", id],
    queryFn: () => papersApi.filteringPrompt(id!),
    select: (res) => res.data,
    enabled: !!id && showFilterPrompt,
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs", id],
    queryFn: () => jobsApi.list(id!, 1, 10),
    select: (res) => res.data,
    enabled: !!id,
    refetchInterval: activeJob ? 5000 : false,
  });

  // ── WebSocket ────────────────────────────────────────────────────────────
  const onProgress = useCallback(
    (event: ProgressEvent) => {
      setActiveJob((prev) =>
        prev && prev.id === event.job_id
          ? { ...prev, progress: event.progress, message: event.message, status: event.status }
          : prev
      );

      if (event.status === "completed" || event.status === "failed" || event.status === "cancelled") {
        queryClient.invalidateQueries({ queryKey: ["project", id] });
        queryClient.invalidateQueries({ queryKey: ["papers", id] });
        queryClient.invalidateQueries({ queryKey: ["paper-stats", id] });
        queryClient.invalidateQueries({ queryKey: ["jobs", id] });

        if (event.status === "completed") {
          toast.success(`Job completed: ${event.message}`);
        } else if (event.status === "cancelled") {
          toast("Job cancelled", { icon: "\u{1F6D1}" });
        } else {
          toast.error(`Job failed: ${event.message}`);
        }
        setActiveJob(null);
      }
    },
    [id, queryClient]
  );

  useWebSocket({
    projectId: id ?? "",
    onProgress,
    enabled: !!activeJob,
  });

  // ── Mutations ────────────────────────────────────────────────────────────
  const launchJob = useMutation({
    mutationFn: (jobType: JobType) => jobsApi.create(id!, jobType),
    onSuccess: (res) => {
      const job = res.data;
      setActiveJob({
        id: job.id,
        type: job.job_type,
        progress: 0,
        message: "Starting...",
        status: "pending",
      });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      toast.success(`${job.job_type} job launched`);
    },
    onError: (err) => toast.error(getErrorMessage(err)),
  });

  const cancelJob = useMutation({
    mutationFn: () => {
      if (!activeJob) throw new Error("No active job");
      return jobsApi.cancel(id!, activeJob.id);
    },
    onSuccess: () => {
      setActiveJob(null);
      queryClient.invalidateQueries({ queryKey: ["project", id] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      toast("Job cancelled", { icon: "\u{1F6D1}" });
    },
    onError: (err) => toast.error(getErrorMessage(err)),
  });

  const deleteProject = useMutation({
    mutationFn: () => projectsApi.delete(id!),
    onSuccess: () => {
      toast.success("Project deleted");
      navigate("/projects");
    },
    onError: (err) => toast.error(getErrorMessage(err)),
  });

  const handleDeleteProject = () => {
    if (confirm(`Delete project "${projectQuery.data?.name}"? All data will be permanently lost.`)) {
      deleteProject.mutate();
    }
  };

  // ── Handlers ─────────────────────────────────────────────────────────────
  const handleExport = async (format: "xlsx" | "csv") => {
    try {
      const res = await exportsApi.download(id!, format);
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `papers.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Exported as ${format.toUpperCase()}`);
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  };

  const handleSearch = () => {
    setFilters({ ...filters, search: searchInput || undefined });
    setPaperPage(1);
  };

  const handleFilterChange = (key: keyof PaperFilters, value: string | undefined) => {
    const updated = { ...filters, [key]: value || undefined };
    setFilters(updated);
    setPaperPage(1);
  };

  // ── Loading ──────────────────────────────────────────────────────────────
  if (projectQuery.isLoading) return <PageSpinner />;
  if (!projectQuery.data) return <EmptyState icon={FileText} title="Not found" description="Project not found." />;

  const project = projectQuery.data;
  const papers = papersQuery.data;
  const stats = statsQuery.data;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <button onClick={() => navigate("/projects")} className="btn-ghost btn-sm">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-gray-900">{project.name}</h1>
              <StatusBadge status={project.status} />
            </div>
            <p className="text-sm text-gray-500 mt-0.5">
              {project.topic} &middot; {project.year_start}–{project.year_end} &middot;{" "}
              <span className="uppercase">{project.sources}</span>
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={() => handleExport("xlsx")} className="btn-secondary btn-sm">
            <Download className="h-4 w-4" />
            XLSX
          </button>
          <button onClick={() => handleExport("csv")} className="btn-secondary btn-sm">
            <Download className="h-4 w-4" />
            CSV
          </button>
          <button
            onClick={handleDeleteProject}
            disabled={deleteProject.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 hover:border-red-300 transition-colors disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" />
            Delete
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Collected" value={project.total_collected} icon={Layers} color="blue" />
        <StatCard label="Unique" value={project.total_after_dedup} icon={Copy} color="purple" />
        <StatCard label="Relevant" value={project.total_after_filter} icon={CheckCircle2} color="green" />
        <StatCard label="With PDF" value={project.total_with_pdf} icon={LinkIcon} color="yellow" />
      </div>

      {/* Pipeline Controls */}
      <div className="card p-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-4">Pipeline</h2>

        {activeJob && (
          <div className="mb-6 rounded-lg bg-brand-50 border border-brand-200 p-4 space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium text-brand-900">
                Running: {activeJob.type.replace(/_/g, " ")}
              </span>
              <div className="flex items-center gap-3">
                <span className="text-brand-600">{Math.round(activeJob.progress)}%</span>
                <button
                  onClick={() => cancelJob.mutate()}
                  disabled={cancelJob.isPending}
                  className="inline-flex items-center gap-1 rounded-md bg-red-100 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-200 transition-colors disabled:opacity-50"
                  title="Cancel this job"
                >
                  <StopCircle className="h-3.5 w-3.5" />
                  Cancel
                </button>
              </div>
            </div>
            <ProgressBar value={activeJob.progress} />
            <p className="text-xs text-brand-700">{activeJob.message}</p>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {PIPELINE_STEPS.map((step) => (
            <button
              key={step.type}
              onClick={() => launchJob.mutate(step.type)}
              disabled={!!activeJob || launchJob.isPending}
              className="flex items-start gap-3 rounded-lg border border-gray-200 p-4 text-left hover:border-brand-300 hover:bg-brand-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className="rounded-lg bg-brand-100 p-2">
                <step.icon className="h-4 w-4 text-brand-600" />
              </div>
              <div>
                <p className="text-sm font-medium text-gray-900">{step.label}</p>
                <p className="text-xs text-gray-500 mt-0.5">{step.desc}</p>
              </div>
            </button>
          ))}
        </div>

        {/* Manual Filtering Workflow */}
        <div className="mt-6 rounded-lg border-2 border-dashed border-amber-300 bg-amber-50 p-5">
          <div className="flex items-start gap-3">
            <div className="rounded-lg bg-amber-100 p-2">
              <Filter className="h-5 w-5 text-amber-600" />
            </div>
            <div className="flex-1">
              <h3 className="text-sm font-semibold text-amber-900">
                Step 3: Manual Relevance Filtering
              </h3>
              <p className="text-xs text-amber-700 mt-1 leading-relaxed">
                After collecting and deduplicating, download the XLSX file and
                upload it to <strong>Claude</strong> (claude.ai) or{" "}
                <strong>ChatGPT</strong> with the prompt below. The AI will mark
                each paper as KEEP or REMOVE based on software security relevance.
              </p>

              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  onClick={() => handleExport("xlsx")}
                  className="inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 transition-colors"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download XLSX
                </button>
                <button
                  onClick={() => setShowFilterPrompt(!showFilterPrompt)}
                  className="inline-flex items-center gap-1.5 rounded-md bg-white border border-amber-300 px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100 transition-colors"
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                  {showFilterPrompt ? "Hide Prompt" : "Show Prompt for Claude/ChatGPT"}
                </button>
              </div>

              {showFilterPrompt && promptQuery.data && (
                <div className="mt-4 space-y-3">
                  <div className="rounded-md bg-white border border-amber-200 p-4">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-amber-900 uppercase tracking-wide">
                        Prompt to paste
                      </span>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(promptQuery.data.prompt);
                          toast.success("Prompt copied to clipboard!");
                        }}
                        className="inline-flex items-center gap-1 rounded bg-amber-100 px-2 py-1 text-xs text-amber-700 hover:bg-amber-200 transition-colors"
                      >
                        <ClipboardCopy className="h-3 w-3" />
                        Copy
                      </button>
                    </div>
                    <pre className="whitespace-pre-wrap text-xs text-gray-700 leading-relaxed font-mono bg-gray-50 rounded p-3 max-h-64 overflow-y-auto">
                      {promptQuery.data.prompt}
                    </pre>
                  </div>
                  <div className="rounded-md bg-white border border-amber-200 p-4">
                    <span className="text-xs font-semibold text-amber-900 uppercase tracking-wide">
                      Instructions
                    </span>
                    <pre className="whitespace-pre-wrap text-xs text-gray-700 leading-relaxed mt-2">
                      {promptQuery.data.instructions}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Full pipeline button */}
        <div className="mt-4 flex justify-center">
          <button
            onClick={() => launchJob.mutate("full_pipeline")}
            disabled={!!activeJob || launchJob.isPending}
            className="btn-primary"
          >
            <Play className="h-4 w-4" />
            Run Full Pipeline (Collect + Dedup + Find PDFs)
          </button>
        </div>
      </div>

      {/* Papers Table */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            Papers{" "}
            {papers && (
              <span className="text-sm font-normal text-gray-400">
                ({papers.total.toLocaleString()})
              </span>
            )}
          </h2>

          {/* Search + filters */}
          <div className="flex flex-wrap gap-2">
            <div className="relative">
              <input
                className="input pl-9 w-48"
                placeholder="Search papers..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              />
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            </div>

            <select
              className="input w-auto"
              value={filters.source ?? ""}
              onChange={(e) => handleFilterChange("source", e.target.value)}
            >
              <option value="">All sources</option>
              <option value="acm">ACM</option>
              <option value="ieee">IEEE</option>
              <option value="springer">Springer</option>
            </select>

            <select
              className="input w-auto"
              value={filters.relevance ?? ""}
              onChange={(e) => handleFilterChange("relevance", e.target.value)}
            >
              <option value="">All relevance</option>
              <option value="keep">Keep</option>
              <option value="remove">Remove</option>
              <option value="pending">Pending</option>
            </select>

            {Object.values(filters).some(Boolean) && (
              <button
                onClick={() => {
                  setFilters({});
                  setSearchInput("");
                  setPaperPage(1);
                }}
                className="btn-ghost btn-sm text-red-500"
              >
                <XCircle className="h-4 w-4" />
                Clear
              </button>
            )}
          </div>
        </div>

        {/* Table */}
        {!papers || papers.items.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="No papers"
            description={
              Object.values(filters).some(Boolean)
                ? "No papers match your filters."
                : "Run the collection step to start gathering papers."
            }
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    <th className="px-6 py-3">Title</th>
                    <th className="px-4 py-3 w-20">Year</th>
                    <th className="px-4 py-3 w-24">Source</th>
                    <th className="px-4 py-3 w-24">Relevance</th>
                    <th className="px-4 py-3 w-16">PDF</th>
                    <th className="px-4 py-3 w-16">Dup</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {papers.items.map((paper) => (
                    <tr
                      key={paper.id}
                      className="hover:bg-gray-50 transition-colors"
                    >
                      <td className="px-6 py-3">
                        <p className="font-medium text-gray-900 line-clamp-1">
                          {paper.title}
                        </p>
                        <p className="text-xs text-gray-400 mt-0.5 truncate">
                          {paper.authors}
                        </p>
                      </td>
                      <td className="px-4 py-3 text-gray-600">
                        {paper.year ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        <span className="badge-blue uppercase">
                          {paper.source}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={paper.relevance} />
                      </td>
                      <td className="px-4 py-3">
                        {paper.pdf_link ? (
                          <a
                            href={paper.pdf_link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-brand-600 hover:text-brand-500"
                          >
                            <LinkIcon className="h-4 w-4" />
                          </a>
                        ) : (
                          <span className="text-gray-300">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {paper.is_duplicate ? (
                          <span className="badge-red">dup</span>
                        ) : (
                          <span className="text-gray-300">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {papers.pages > 1 && (
              <div className="flex items-center justify-between border-t border-gray-100 px-6 py-3 text-sm text-gray-500">
                <span>
                  Page {papers.page} of {papers.pages}
                </span>
                <div className="flex gap-1">
                  <button
                    onClick={() => setPaperPage((p) => Math.max(1, p - 1))}
                    disabled={papers.page <= 1}
                    className="btn-ghost btn-sm"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() =>
                      setPaperPage((p) => Math.min(papers.pages, p + 1))
                    }
                    disabled={papers.page >= papers.pages}
                    className="btn-ghost btn-sm"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Recent Jobs */}
      {jobsQuery.data && jobsQuery.data.items.length > 0 && (
        <div className="card">
          <div className="border-b border-gray-200 px-6 py-4">
            <h2 className="text-sm font-semibold text-gray-900">
              Recent Jobs
            </h2>
          </div>
          <div className="divide-y divide-gray-50">
            {jobsQuery.data.items.map((job) => (
              <div
                key={job.id}
                className="flex items-center justify-between px-6 py-3"
              >
                <div className="flex items-center gap-3">
                  <StatusBadge status={job.status} />
                  <span className="text-sm text-gray-700">
                    {job.job_type.replace(/_/g, " ")}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-gray-400">
                  {job.status === "running" && (
                    <span className="text-brand-600 font-medium">
                      {Math.round(job.progress)}%
                    </span>
                  )}
                  <span>
                    {new Date(job.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stats Breakdown */}
      {stats && stats.total > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {/* By source */}
          <div className="card p-5">
            <h3 className="text-sm font-semibold text-gray-900 mb-3">
              By Source
            </h3>
            <div className="space-y-2">
              {Object.entries(stats.by_source).map(([source, count]) => (
                <div
                  key={source}
                  className="flex items-center justify-between text-sm"
                >
                  <span className="uppercase text-gray-600">{source}</span>
                  <span className="font-medium text-gray-900">{count}</span>
                </div>
              ))}
            </div>
          </div>
          {/* By relevance */}
          <div className="card p-5">
            <h3 className="text-sm font-semibold text-gray-900 mb-3">
              By Relevance
            </h3>
            <div className="space-y-2">
              {Object.entries(stats.by_relevance).map(
                ([relevance, count]) => (
                  <div
                    key={relevance}
                    className="flex items-center justify-between text-sm"
                  >
                    <StatusBadge status={relevance} />
                    <span className="font-medium text-gray-900">{count}</span>
                  </div>
                )
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
