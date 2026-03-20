import { useState, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { projectsApi, papersApi, jobsApi, exportsApi, uploadApi } from "@/api/endpoints";
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
  Copy,
  CheckCircle2,
  Link as LinkIcon,
  Layers,
  ChevronLeft,
  ChevronRight,
  Search,
  XCircle,
  StopCircle,
  Trash2,
  Upload,
  ShieldCheck,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import type { ProgressEvent, JobType, PaperFilters } from "@/types";

const PIPELINE_STEPS: {
  key: string;
  label: string;
  icon: React.ElementType;
  desc: string;
}[] = [
  {
    key: "collect",
    label: "1. Collect",
    icon: Layers,
    desc: "Collect papers from selected sources via APIs",
  },
  {
    key: "deduplicate",
    label: "2. Deduplicate",
    icon: Copy,
    desc: "Find and mark duplicates (DOI + normalized title + TF-IDF similarity)",
  },
  {
    key: "automated_exclusion",
    label: "3. Automated Exclusion",
    icon: ShieldCheck,
    desc: "AI-powered exclusion of non-software-security papers (Groq)",
  },
  {
    key: "find_links",
    label: "4. Find PDFs",
    icon: LinkIcon,
    desc: "Locate open-access PDF links from multiple sources",
  },
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

  // Step selection state
  const [selectedSteps, setSelectedSteps] = useState<Set<string>>(new Set());
  const [showImportModal, setShowImportModal] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
    mutationFn: ({ jobType, steps }: { jobType: JobType; steps?: string[] }) =>
      jobsApi.create(id!, jobType, steps),
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
      toast.success(`Pipeline job launched`);
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
      queryClient.invalidateQueries({ queryKey: ["projects"] });
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

  // ── Step selection handlers ──────────────────────────────────────────────
  const toggleStep = (stepKey: string) => {
    setSelectedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepKey)) {
        next.delete(stepKey);
      } else {
        next.add(stepKey);
      }
      return next;
    });
  };

  const handleStartPipeline = async () => {
    if (selectedSteps.size === 0) {
      toast.error("Please select at least one pipeline step");
      return;
    }

    const steps = PIPELINE_STEPS
      .filter((s) => selectedSteps.has(s.key))
      .map((s) => s.key);

    // If "collect" is not selected, user must import a file
    if (!steps.includes("collect")) {
      // Check if project already has papers
      const hasData = (project?.total_collected ?? 0) > 0;
      if (!hasData) {
        setShowImportModal(true);
        return;
      }
    }

    // Launch custom pipeline
    if (steps.length === 1) {
      launchJob.mutate({ jobType: steps[0] as JobType });
    } else {
      launchJob.mutate({ jobType: "custom_pipeline", steps });
    }
  };

  const handleFileUpload = async () => {
    if (!importFile) {
      toast.error("Please select a file");
      return;
    }
    setIsUploading(true);
    try {
      const res = await uploadApi.uploadPapers(id!, importFile);
      toast.success(`Imported ${res.data.imported} papers`);
      if (res.data.columns_unmapped.length > 0) {
        toast(`Unmapped columns: ${res.data.columns_unmapped.join(", ")}`, {
          icon: "\u2139\uFE0F",
          duration: 5000,
        });
      }
      queryClient.invalidateQueries({ queryKey: ["project", id] });
      queryClient.invalidateQueries({ queryKey: ["papers", id] });
      queryClient.invalidateQueries({ queryKey: ["paper-stats", id] });
      setShowImportModal(false);
      setImportFile(null);

      // Now launch the remaining steps (without collect)
      const steps = PIPELINE_STEPS
        .filter((s) => selectedSteps.has(s.key) && s.key !== "collect")
        .map((s) => s.key);
      if (steps.length === 1) {
        launchJob.mutate({ jobType: steps[0] as JobType });
      } else if (steps.length > 1) {
        launchJob.mutate({ jobType: "custom_pipeline", steps });
      }
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setIsUploading(false);
    }
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
        <StatCard label="After Exclusion" value={project.total_after_filter} icon={ShieldCheck} color="green" />
        <StatCard label="With PDF" value={project.total_with_pdf} icon={LinkIcon} color="yellow" />
      </div>

      {/* Pipeline Controls */}
      <div className="card p-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-2">Pipeline</h2>
        <p className="text-xs text-gray-500 mb-4">
          Select the steps you want to run, then click Start. If you skip Collection, you'll be asked to import a file.
        </p>

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

        {/* Step Selection Grid */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {PIPELINE_STEPS.map((step) => {
            const isSelected = selectedSteps.has(step.key);
            return (
              <button
                key={step.key}
                onClick={() => toggleStep(step.key)}
                disabled={!!activeJob}
                className={`
                  relative flex items-start gap-3 rounded-lg border-2 p-4 text-left transition-all
                  disabled:opacity-50 disabled:cursor-not-allowed
                  ${isSelected
                    ? "border-brand-500 bg-brand-50 ring-1 ring-brand-200"
                    : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                  }
                `}
              >
                {/* Checkbox indicator */}
                <div
                  className={`
                    mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border-2 transition-colors
                    ${isSelected
                      ? "border-brand-500 bg-brand-500"
                      : "border-gray-300"
                    }
                  `}
                >
                  {isSelected && (
                    <CheckCircle2 className="h-3.5 w-3.5 text-white" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <step.icon className={`h-4 w-4 ${isSelected ? "text-brand-600" : "text-gray-400"}`} />
                    <p className={`text-sm font-medium ${isSelected ? "text-brand-900" : "text-gray-900"}`}>
                      {step.label}
                    </p>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">{step.desc}</p>
                </div>
              </button>
            );
          })}
        </div>

        {/* Start button */}
        <div className="mt-5 flex items-center justify-between">
          <div className="text-xs text-gray-500">
            {selectedSteps.size === 0
              ? "No steps selected"
              : `${selectedSteps.size} step${selectedSteps.size > 1 ? "s" : ""} selected`}
            {selectedSteps.size > 0 && !selectedSteps.has("collect") && (
              <span className="ml-2 text-amber-600 font-medium">
                (file import required)
              </span>
            )}
          </div>
          <button
            onClick={handleStartPipeline}
            disabled={!!activeJob || launchJob.isPending || selectedSteps.size === 0}
            className="btn-primary"
          >
            <Play className="h-4 w-4" />
            Start Pipeline
          </button>
        </div>
      </div>

      {/* File Import Modal */}
      {showImportModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-900">Import Papers</h3>
                <p className="text-sm text-gray-500 mt-0.5">
                  Since you're skipping Collection, upload your papers file
                </p>
              </div>
              <button
                onClick={() => {
                  setShowImportModal(false);
                  setImportFile(null);
                }}
                className="rounded-lg p-1 hover:bg-gray-100 transition-colors"
              >
                <X className="h-5 w-5 text-gray-400" />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <div className="rounded-lg border-2 border-dashed border-gray-300 p-8 text-center hover:border-brand-400 transition-colors">
                <Upload className="mx-auto h-10 w-10 text-gray-400" />
                <p className="mt-3 text-sm text-gray-600">
                  Upload a <strong>CSV</strong> or <strong>XLSX</strong> file
                </p>
                <p className="mt-1 text-xs text-gray-400">
                  Columns will be auto-mapped. Missing columns (DOI, abstract, etc.) will be filled with defaults.
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv,.xlsx,.xls"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) setImportFile(f);
                  }}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
                >
                  <Upload className="h-4 w-4" />
                  Choose File
                </button>
              </div>

              {importFile && (
                <div className="flex items-center gap-3 rounded-lg bg-green-50 border border-green-200 px-4 py-3">
                  <FileText className="h-5 w-5 text-green-600" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-green-900 truncate">
                      {importFile.name}
                    </p>
                    <p className="text-xs text-green-600">
                      {(importFile.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                  <button
                    onClick={() => setImportFile(null)}
                    className="text-green-400 hover:text-green-600"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              )}

              <div className="rounded-lg bg-amber-50 border border-amber-200 p-3">
                <p className="text-xs text-amber-800">
                  <strong>Column mapping:</strong> We'll automatically map columns like "Title", "Year", "Authors", "DOI", "Abstract", "Publisher", etc.
                  Any unrecognized columns will be noted. Missing columns will be filled with empty defaults.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 border-t border-gray-200 px-6 py-4">
              <button
                onClick={() => {
                  setShowImportModal(false);
                  setImportFile(null);
                }}
                className="btn-ghost"
              >
                Cancel
              </button>
              <button
                onClick={handleFileUpload}
                disabled={!importFile || isUploading}
                className="btn-primary"
              >
                {isUploading ? (
                  <>
                    <svg className="animate-spin h-4 w-4 text-white" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    Importing…
                  </>
                ) : (
                  <>
                    <Upload className="h-4 w-4" />
                    Import & Start Pipeline
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

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
