import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { projectsApi } from "@/api/endpoints";
import { getErrorMessage } from "@/api/client";
import { Spinner } from "@/components/ui";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import type { Topic } from "@/types";

export default function NewProject() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [topic, setTopic] = useState<Topic>("detection");
  const [yearStart, setYearStart] = useState(2000);
  const [yearEnd, setYearEnd] = useState(2026);
  const [sources, setSources] = useState<Record<string, boolean>>({
    acm: true,
    ieee: true,
    springer: true,
    elsevier: true,
    wiley: true,
    scopus: false,
    taylor_francis: false,
    mdpi: false,
    sage: false,
  });
  const [customStrings, setCustomStrings] = useState<
    { label: string; query: string }[]
  >([{ label: "", query: "" }]);

  const createMutation = useMutation({
    mutationFn: projectsApi.create,
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Project created!");
      navigate(`/projects/${res.data.id}`);
    },
    onError: (err) => toast.error(getErrorMessage(err)),
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const selectedSources = Object.entries(sources)
      .filter(([, v]) => v)
      .map(([k]) => k)
      .join(",");

    if (!selectedSources) {
      toast.error("Select at least one source");
      return;
    }

    createMutation.mutate({
      name,
      description,
      topic,
      year_start: yearStart,
      year_end: yearEnd,
      sources: selectedSources,
      custom_search_strings:
        topic === "custom"
          ? customStrings.filter((s) => s.label && s.query)
          : undefined,
    });
  };

  const addCustomString = () => {
    setCustomStrings([...customStrings, { label: "", query: "" }]);
  };

  const removeCustomString = (idx: number) => {
    setCustomStrings(customStrings.filter((_, i) => i !== idx));
  };

  const updateCustomString = (
    idx: number,
    field: "label" | "query",
    value: string
  ) => {
    const updated = [...customStrings];
    updated[idx][field] = value;
    setCustomStrings(updated);
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate(-1)}
          className="btn-ghost btn-sm"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-gray-900">New Project</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Configure your systematic mapping study
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="card divide-y divide-gray-100">
        {/* Basic Info */}
        <div className="space-y-4 p-6">
          <h2 className="text-sm font-semibold text-gray-900">
            Basic Information
          </h2>
          <div>
            <label htmlFor="name" className="label">
              Project name
            </label>
            <input
              id="name"
              className="input"
              required
              placeholder="e.g. Vulnerability Detection SMS 2024"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="desc" className="label">
              Description (optional)
            </label>
            <textarea
              id="desc"
              rows={3}
              className="input resize-none"
              placeholder="Brief description of your study..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>

        {/* Topic */}
        <div className="space-y-4 p-6">
          <h2 className="text-sm font-semibold text-gray-900">
            Research Topic
          </h2>
          <div className="grid gap-3 sm:grid-cols-3">
            {(
              [
                {
                  value: "detection",
                  label: "Detection",
                  desc: "Vulnerability detection",
                },
                {
                  value: "remediation",
                  label: "Remediation",
                  desc: "Vulnerability remediation",
                },
                {
                  value: "custom",
                  label: "Custom",
                  desc: "Your own search strings",
                },
              ] as const
            ).map((opt) => (
              <label
                key={opt.value}
                className={`cursor-pointer rounded-lg border-2 p-4 transition-colors ${
                  topic === opt.value
                    ? "border-brand-600 bg-brand-50"
                    : "border-gray-200 hover:border-gray-300"
                }`}
              >
                <input
                  type="radio"
                  name="topic"
                  value={opt.value}
                  checked={topic === opt.value}
                  onChange={(e) => setTopic(e.target.value as Topic)}
                  className="sr-only"
                />
                <p className="text-sm font-medium text-gray-900">
                  {opt.label}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">{opt.desc}</p>
              </label>
            ))}
          </div>

          {/* Custom search strings */}
          {topic === "custom" && (
            <div className="space-y-3 pt-2">
              <p className="text-xs text-gray-500">
                Define your custom search strings below.
              </p>
              {customStrings.map((s, idx) => (
                <div key={idx} className="flex gap-2">
                  <input
                    className="input w-32"
                    placeholder="Label"
                    value={s.label}
                    onChange={(e) =>
                      updateCustomString(idx, "label", e.target.value)
                    }
                  />
                  <input
                    className="input flex-1"
                    placeholder="Search query"
                    value={s.query}
                    onChange={(e) =>
                      updateCustomString(idx, "query", e.target.value)
                    }
                  />
                  {customStrings.length > 1 && (
                    <button
                      type="button"
                      onClick={() => removeCustomString(idx)}
                      className="btn-ghost btn-sm text-red-500"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={addCustomString}
                className="btn-ghost btn-sm text-brand-600"
              >
                <Plus className="h-4 w-4" />
                Add search string
              </button>
            </div>
          )}
        </div>

        {/* Year Range + Sources */}
        <div className="space-y-4 p-6">
          <h2 className="text-sm font-semibold text-gray-900">
            Search Parameters
          </h2>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="yearStart" className="label">
                Start year
              </label>
              <input
                id="yearStart"
                type="number"
                min={1970}
                max={2030}
                className="input"
                value={yearStart}
                onChange={(e) => setYearStart(Number(e.target.value))}
              />
            </div>
            <div>
              <label htmlFor="yearEnd" className="label">
                End year
              </label>
              <input
                id="yearEnd"
                type="number"
                min={1970}
                max={2030}
                className="input"
                value={yearEnd}
                onChange={(e) => setYearEnd(Number(e.target.value))}
              />
            </div>
          </div>

          <div>
            <p className="label">Sources</p>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
              {([
                { key: "acm", label: "ACM" },
                { key: "ieee", label: "IEEE" },
                { key: "springer", label: "Springer" },
                { key: "elsevier", label: "Elsevier" },
                { key: "wiley", label: "Wiley" },
                { key: "scopus", label: "Scopus (broad)" },
                { key: "taylor_francis", label: "Taylor & Francis" },
                { key: "mdpi", label: "MDPI" },
                { key: "sage", label: "SAGE" },
              ] as const).map((src) => (
                <label key={src.key} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                    checked={sources[src.key] ?? false}
                    onChange={(e) =>
                      setSources({ ...sources, [src.key]: e.target.checked })
                    }
                  />
                  <span>{src.label}</span>
                </label>
              ))}
            </div>

          </div>
        </div>

        {/* Submit */}
        <div className="flex justify-end gap-3 p-6">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="btn-secondary"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="btn-primary"
          >
            {createMutation.isPending ? (
              <Spinner className="h-4 w-4 text-white" />
            ) : (
              "Create Project"
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
