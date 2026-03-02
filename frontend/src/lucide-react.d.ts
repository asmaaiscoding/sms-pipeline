declare module "lucide-react" {
  import { FC, SVGProps } from "react";
  interface LucideProps extends SVGProps<SVGSVGElement> {
    size?: number | string;
    color?: string;
    strokeWidth?: number | string;
    absoluteStrokeWidth?: boolean;
    className?: string;
  }
  type LucideIcon = FC<LucideProps>;

  export const Loader2: LucideIcon;
  export const LayoutDashboard: LucideIcon;
  export const FolderKanban: LucideIcon;
  export const LogOut: LucideIcon;
  export const BookOpenCheck: LucideIcon;
  export const Menu: LucideIcon;
  export const X: LucideIcon;
  export const Plus: LucideIcon;
  export const Trash2: LucideIcon;
  export const FileText: LucideIcon;
  export const CheckCircle2: LucideIcon;
  export const AlertCircle: LucideIcon;
  export const Eye: LucideIcon;
  export const EyeOff: LucideIcon;
  export const ArrowLeft: LucideIcon;
  export const Play: LucideIcon;
  export const Download: LucideIcon;
  export const Filter: LucideIcon;
  export const Copy: LucideIcon;
  export const Link: LucideIcon;
  export const Layers: LucideIcon;
  export const ChevronLeft: LucideIcon;
  export const ChevronRight: LucideIcon;
  export const Search: LucideIcon;
  export const XCircle: LucideIcon;
  export const ClipboardCopy: LucideIcon;
  export const MessageSquare: LucideIcon;
  export const StopCircle: LucideIcon;
}
