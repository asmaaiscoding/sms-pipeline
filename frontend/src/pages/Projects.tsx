import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { projectsApi } from "@/api/endpoints";
import { Link } from "react-router-dom";
import { Plus, FolderKanban, Trash2 } from "lucide-react";
import {
  PageSpinner,
  EmptyState,
  StatusBadge,
} from "@/components/ui";
import { getErrorMessage } from "@/api/client";
import toast from "react-hot-toast";
import type { Project } from "@/types";

export default function Projects() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["projects", 1],
    queryFn: () => projectsApi.list(1, 100),
    select: (res) => res.data,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => projectsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Project deleted");
    },
    onError: (err) => toast.error(getErrorMessage(err)),
  });

  const handleDelete = (id: string, name: string) => {
    if (confirm(`Delete project "${name}"? All data will be lost.`)) {
      deleteMutation.mutate(id);
    }
  };

  if (isLoading) return <PageSpinner />;

  const projects = data?.items ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Projects</h1>
          <p className="text-sm text-gray-500 mt-1">
            Manage your systematic mapping study projects
          </p>
        </div>
        <Link to="/projects/new" className="btn-primary">
          <Plus className="h-4 w-4" />
          New Project
        </Link>
      </div>

      {/* Grid */}
      {projects.length === 0 ? (
        <EmptyState
          icon={FolderKanban}
          title="No projects"
          description="Create your first project to begin collecting and analyzing papers."
          action={
            <Link to="/projects/new" className="btn-primary">
              <Plus className="h-4 w-4" />
              New Project
            </Link>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project: Project) => (
            <div key={project.id} className="card group relative">
              <Link
                to={`/projects/${project.id}`}
                className="block p-5 space-y-3"
              >
                <div className="flex items-start justify-between">
                  <h3 className="text-sm font-semibold text-gray-900 truncate pr-8">
                    {project.name}
                  </h3>
                  <StatusBadge status={project.status} />
                </div>

                {project.description && (
                  <p className="text-xs text-gray-500 line-clamp-2">
                    {project.description}
                  </p>
                )}

                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
                  <span>
                    Topic:{" "}
                    <span className="text-gray-600">{project.topic}</span>
                  </span>
                  <span>
                    Years:{" "}
                    <span className="text-gray-600">
                      {project.year_start}–{project.year_end}
                    </span>
                  </span>
                  <span>
                    Sources:{" "}
                    <span className="text-gray-600 uppercase">
                      {project.sources}
                    </span>
                  </span>
                </div>

                <div className="grid grid-cols-4 gap-2 pt-2 border-t border-gray-100 text-center">
                  <div>
                    <p className="text-lg font-bold text-gray-900">
                      {project.total_collected}
                    </p>
                    <p className="text-[10px] text-gray-400 uppercase tracking-wider">
                      Collected
                    </p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-gray-900">
                      {project.total_after_dedup}
                    </p>
                    <p className="text-[10px] text-gray-400 uppercase tracking-wider">
                      Unique
                    </p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-gray-900">
                      {project.total_after_filter}
                    </p>
                    <p className="text-[10px] text-gray-400 uppercase tracking-wider">
                      Relevant
                    </p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-gray-900">
                      {project.total_with_pdf}
                    </p>
                    <p className="text-[10px] text-gray-400 uppercase tracking-wider">
                      PDFs
                    </p>
                  </div>
                </div>
              </Link>

              {/* Delete button */}
              <button
                onClick={(e) => {
                  e.preventDefault();
                  handleDelete(project.id, project.name);
                }}
                className="absolute top-4 right-4 rounded-lg p-1.5 text-gray-300 opacity-0 group-hover:opacity-100 hover:bg-red-50 hover:text-red-500 transition-all"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
