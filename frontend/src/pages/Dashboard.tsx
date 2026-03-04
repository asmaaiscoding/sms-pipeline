import { useQuery } from "@tanstack/react-query";
import { projectsApi } from "@/api/endpoints";
import { Link } from "react-router-dom";
import {
  FolderKanban,
  FileText,
  CheckCircle2,
  AlertCircle,
  Plus,
} from "lucide-react";
import { StatCard, PageSpinner, StatusBadge, EmptyState } from "@/components/ui";
import type { Project } from "@/types";

export default function Dashboard() {
  const { data, isLoading } = useQuery({
    queryKey: ["projects", 1],
    queryFn: () => projectsApi.list(1, 100),
    select: (res) => res.data,
    refetchOnWindowFocus: true,
  });

  if (isLoading) return <PageSpinner />;

  const projects = data?.items ?? [];
  const totalPapers = projects.reduce((s, p) => s + p.total_collected, 0);
  const completedCount = projects.filter(
    (p) => p.status === "completed"
  ).length;
  const failedCount = projects.filter((p) => p.status === "failed").length;

  const recentProjects = projects.slice(0, 5);

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">
            Overview of your systematic mapping studies
          </p>
        </div>
        <Link to="/projects/new" className="btn-primary">
          <Plus className="h-4 w-4" />
          New Project
        </Link>
      </div>

      {/* Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total Projects"
          value={projects.length}
          icon={FolderKanban}
          color="blue"
        />
        <StatCard
          label="Total Papers"
          value={totalPapers.toLocaleString()}
          icon={FileText}
          color="purple"
        />
        <StatCard
          label="Completed"
          value={completedCount}
          icon={CheckCircle2}
          color="green"
        />
        <StatCard
          label="Failed"
          value={failedCount}
          icon={AlertCircle}
          color="red"
        />
      </div>

      {/* Recent Projects */}
      <div className="card">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            Recent Projects
          </h2>
        </div>
        {recentProjects.length === 0 ? (
          <EmptyState
            icon={FolderKanban}
            title="No projects yet"
            description="Create your first systematic mapping study project to get started."
            action={
              <Link to="/projects/new" className="btn-primary">
                <Plus className="h-4 w-4" />
                New Project
              </Link>
            }
          />
        ) : (
          <div className="divide-y divide-gray-100">
            {recentProjects.map((project: Project) => (
              <Link
                key={project.id}
                to={`/projects/${project.id}`}
                className="flex items-center justify-between px-6 py-4 hover:bg-gray-50 transition-colors"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 truncate">
                    {project.name}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {project.topic} &middot;{" "}
                    {project.total_collected.toLocaleString()} papers &middot;{" "}
                    {new Date(project.created_at).toLocaleDateString()}
                  </p>
                </div>
                <StatusBadge status={project.status} />
              </Link>
            ))}
          </div>
        )}
        {projects.length > 5 && (
          <div className="border-t border-gray-100 px-6 py-3">
            <Link
              to="/projects"
              className="text-sm font-medium text-brand-600 hover:text-brand-500"
            >
              View all projects &rarr;
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
