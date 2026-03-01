import { Loader2 } from "lucide-react";
import clsx from "clsx";

export function Spinner({ className }: { className?: string }) {
  return (
    <Loader2
      className={clsx("h-5 w-5 animate-spin text-brand-600", className)}
    />
  );
}

export function PageSpinner() {
  return (
    <div className="flex h-64 items-center justify-center">
      <Spinner className="h-8 w-8" />
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 rounded-xl bg-gray-100 p-4">
        <Icon className="h-8 w-8 text-gray-400" />
      </div>
      <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-gray-500">{description}</p>
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}

export function StatCard({
  label,
  value,
  icon: Icon,
  color = "blue",
}: {
  label: string;
  value: number | string;
  icon: React.ElementType;
  color?: "blue" | "green" | "yellow" | "red" | "purple";
}) {
  const colorClasses = {
    blue: "bg-blue-50 text-blue-600",
    green: "bg-green-50 text-green-600",
    yellow: "bg-yellow-50 text-yellow-600",
    red: "bg-red-50 text-red-600",
    purple: "bg-purple-50 text-purple-600",
  };

  return (
    <div className="card p-5">
      <div className="flex items-center gap-4">
        <div className={clsx("rounded-lg p-2.5", colorClasses[color])}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <p className="text-sm text-gray-500">{label}</p>
          <p className="text-2xl font-bold text-gray-900">{value}</p>
        </div>
      </div>
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    draft: "badge-gray",
    collecting: "badge-blue",
    deduplicating: "badge-blue",
    filtering: "badge-yellow",
    finding_links: "badge-yellow",
    completed: "badge-green",
    failed: "badge-red",
    pending: "badge-gray",
    running: "badge-blue",
    cancelled: "badge-red",
    keep: "badge-green",
    remove: "badge-red",
  };

  return (
    <span className={styles[status] ?? "badge-gray"}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function ProgressBar({
  value,
  className,
}: {
  value: number;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "h-2 w-full overflow-hidden rounded-full bg-gray-100",
        className
      )}
    >
      <div
        className="h-full rounded-full bg-brand-600 transition-all duration-300"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}
